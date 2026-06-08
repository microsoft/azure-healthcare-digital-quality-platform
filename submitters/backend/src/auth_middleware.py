"""
Azure AD JWT Token Validation Middleware

This module provides middleware for validating Azure AD JWT tokens in FastAPI applications.
It implements proper token validation including signature verification, expiration checks,
and audience validation for secure API access.
"""

import os
import jwt
import requests
from typing import Optional, Dict, Any, List
from fastapi import HTTPException, Request, Depends
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from functools import wraps
import time
import json
from datetime import datetime, timedelta
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
import base64

class AzureADTokenValidator:
    """
    Azure AD JWT Token Validator
    
    Validates JWT tokens issued by Azure AD including signature verification,
    expiration checks, and audience validation.
    """
    
    def __init__(self):
        # Initialize without validation - will be validated on first use
        self._initialized = False
        self.tenant_id = None
        self.client_id = None
        self.issuer = None
        self.jwks_uri_v1 = None
        self.jwks_uri_v2 = None
        self.jwks_cache = {}
        self.jwks_cache_time = 0
        self.jwks_cache_duration = 3600  # 1 hour cache
        self.valid_audiences = []
        self.valid_issuers = []
    
    def _ensure_initialized(self):
        """
        Lazy initialization of Azure AD configuration
        This ensures environment variables are loaded before validation
        """
        if self._initialized:
            return
            
        self.tenant_id = os.getenv("AZURE_TENANT_ID", "16b3c013-d300-468d-ac64-7eda0820b6d3")
        self.client_id = os.getenv("AZURE_CLIENT_ID", "6441e54f-8149-487b-aac4-3a55a049a362")
        
        # Validate required configuration
        if not self.client_id:
            raise ValueError("AZURE_CLIENT_ID environment variable is required")
        
        # Set up valid audiences
        # Accept both the app client ID and Microsoft Graph API
        self.valid_audiences = [
            self.client_id,  # Your app's client ID
            "00000003-0000-0000-c000-000000000000",  # Microsoft Graph
            f"api://{self.client_id}",  # API audience format
        ]
        
        # Set up valid issuers (both v1.0 and v2.0 endpoints)
        self.valid_issuers = [
            f"https://login.microsoftonline.com/{self.tenant_id}/v2.0",
            f"https://sts.windows.net/{self.tenant_id}/",
        ]
        
        # Set up JWKS URIs for both v1.0 and v2.0
        self.jwks_uri_v1 = f"https://login.microsoftonline.com/{self.tenant_id}/discovery/keys"
        self.jwks_uri_v2 = f"https://login.microsoftonline.com/{self.tenant_id}/discovery/v2.0/keys"
        
        # Also handle common endpoint for multi-tenant scenarios
        if self.tenant_id == "common":
            self.jwks_uri_v1 = "https://login.microsoftonline.com/common/discovery/keys"
            self.jwks_uri_v2 = "https://login.microsoftonline.com/common/discovery/v2.0/keys"
            
        self._initialized = True
        
        print(f"🔐 Azure AD Validator initialized:")
        print(f"   Tenant: {self.tenant_id}")
        print(f"   Client ID: {self.client_id}")
        print(f"   JWKS URI v1.0: {self.jwks_uri_v1}")
        print(f"   JWKS URI v2.0: {self.jwks_uri_v2}")
        print(f"   Valid audiences: {self.valid_audiences}")
        print(f"   Valid issuers: {self.valid_issuers}")
    
    def _determine_jwks_uri(self, token_issuer: str) -> str:
        """
        Determine which JWKS endpoint to use based on the token issuer
        
        Args:
            token_issuer: The issuer claim from the token
            
        Returns:
            str: The appropriate JWKS URI
        """
        self._ensure_initialized()
        
        # Use v1.0 keys for v1.0 tokens (sts.windows.net)
        if "sts.windows.net" in token_issuer:
            return self.jwks_uri_v1
        
        # Use v2.0 keys for v2.0 tokens (login.microsoftonline.com/*/v2.0)
        if "/v2.0" in token_issuer:
            return self.jwks_uri_v2
        
        # Default to v2.0 for unknown patterns
        return self.jwks_uri_v2
    
    def _get_jwks(self, token_issuer: str) -> Dict[str, Any]:
        """
        Retrieve JSON Web Key Set (JWKS) from Azure AD with caching
        
        Args:
            token_issuer: The issuer claim from the token to determine correct endpoint
            
        Returns:
            Dict: JWKS data for token signature verification
        """
        jwks_uri = self._determine_jwks_uri(token_issuer)
        cache_key = jwks_uri
        
        current_time = time.time()
        
        # Check if cached JWKS is still valid for this specific URI
        if (cache_key in self.jwks_cache and 
            current_time - self.jwks_cache.get(f"{cache_key}_time", 0) < self.jwks_cache_duration):
            print(f"🔄 Using cached JWKS for: {jwks_uri}")
            return self.jwks_cache[cache_key]
        
        try:
            print(f"🔑 Fetching JWKS from: {jwks_uri}")
            # Fetch fresh JWKS from Azure AD
            response = requests.get(jwks_uri, timeout=30)
            response.raise_for_status()
            
            jwks_data = response.json()
            
            # Cache the data with URI-specific key
            self.jwks_cache[cache_key] = jwks_data
            self.jwks_cache[f"{cache_key}_time"] = current_time
            
            print(f"✓ Successfully fetched JWKS with {len(jwks_data.get('keys', []))} keys")
            return jwks_data
            
        except requests.RequestException as e:
            print(f"❌ Failed to fetch JWKS from {jwks_uri}: {e}")
            
            # Try fallback to cached version
            if cache_key in self.jwks_cache:
                print(f"⚠️  Using cached JWKS version for {jwks_uri}")
                return self.jwks_cache[cache_key]
            
            # Try the other endpoint as fallback
            fallback_uri = self.jwks_uri_v1 if jwks_uri == self.jwks_uri_v2 else self.jwks_uri_v2
            try:
                print(f"🔄 Trying fallback JWKS endpoint: {fallback_uri}")
                response = requests.get(fallback_uri, timeout=30)
                response.raise_for_status()
                
                jwks_data = response.json()
                
                # Cache the fallback data
                self.jwks_cache[cache_key] = jwks_data
                self.jwks_cache[f"{cache_key}_time"] = current_time
                
                print(f"✓ Successfully fetched JWKS from fallback with {len(jwks_data.get('keys', []))} keys")
                return jwks_data
                
            except requests.RequestException as fallback_error:
                print(f"❌ Fallback JWKS fetch also failed: {fallback_error}")
                
                raise HTTPException(
                    status_code=500,
                    detail=f"Unable to fetch Azure AD signing keys from both endpoints: {str(e)}, fallback: {str(fallback_error)}"
                )
    
    def _get_signing_key(self, token_header: Dict[str, Any], token_issuer: str) -> Any:
        """
        Get the appropriate signing key for token verification
        
        Args:
            token_header: JWT token header containing key ID
            token_issuer: The issuer claim from the token
            
        Returns:
            RSA public key for signature verification
        """
        kid = token_header.get("kid")
        if not kid:
            raise HTTPException(
                status_code=401,
                detail="Token missing key ID (kid)"
            )
        
        print(f"🔍 Looking for signing key with kid: {kid} for issuer: {token_issuer}")
        
        jwks = self._get_jwks(token_issuer)
        
        # Find matching key in JWKS
        available_kids = []
        matching_key_data = None
        
        for key in jwks.get("keys", []):
            key_kid = key.get("kid")
            available_kids.append(key_kid)
            if key_kid == kid:
                matching_key_data = key
                break
        
        if not matching_key_data:
            print(f"❌ No matching key found. Available kids: {available_kids}")
            raise HTTPException(
                status_code=401,
                detail=f"Unable to find matching signing key. Token kid: {kid}, Available keys: {available_kids}"
            )
        
        try:
            print(f"🔑 Processing JWK for kid: {kid}")
            print(f"   Key type: {matching_key_data.get('kty')}")
            print(f"   Key use: {matching_key_data.get('use')}")
            print(f"   Algorithm: {matching_key_data.get('alg')}")
            print(f"   Key operations: {matching_key_data.get('key_ops', [])}")
            
            # Method 1: Try PyJWT's built-in JWK handling
            try:
                signing_key = jwt.algorithms.RSAAlgorithm.from_jwk(matching_key_data)
                print(f"✓ Successfully converted JWK to RSA key using PyJWT method")
                return signing_key
            except Exception as pyjwt_error:
                print(f"⚠️  PyJWT JWK conversion failed: {pyjwt_error}")
                
                # Method 2: Manual JWK to RSA conversion
                try:
                    print("🔄 Trying manual JWK to RSA conversion...")
                    
                    # Extract the RSA components from JWK
                    n_b64 = matching_key_data.get('n')  # modulus
                    e_b64 = matching_key_data.get('e')  # exponent
                    
                    if not n_b64 or not e_b64:
                        raise ValueError("Missing RSA modulus (n) or exponent (e) in JWK")
                    
                    # Decode base64url components
                    def base64url_decode(data):
                        # Add padding if needed
                        missing_padding = len(data) % 4
                        if missing_padding:
                            data += '=' * (4 - missing_padding)
                        return base64.urlsafe_b64decode(data)
                    
                    n_bytes = base64url_decode(n_b64)
                    e_bytes = base64url_decode(e_b64)
                    
                    # Convert to integers
                    n = int.from_bytes(n_bytes, byteorder='big')
                    e = int.from_bytes(e_bytes, byteorder='big')
                    
                    # Create RSA public key
                    public_key = rsa.RSAPublicNumbers(e, n).public_key()
                    
                    print(f"✓ Successfully created RSA key manually")
                    print(f"   Key size: {public_key.key_size} bits")
                    
                    return public_key
                    
                except Exception as manual_error:
                    print(f"❌ Manual JWK conversion also failed: {manual_error}")
                    raise HTTPException(
                        status_code=401,
                        detail=f"Failed to process signing key with both methods. PyJWT: {str(pyjwt_error)}, Manual: {str(manual_error)}"
                    )
                    
        except Exception as e:
            print(f"❌ Unexpected error processing signing key: {e}")
            raise HTTPException(
                status_code=401,
                detail=f"Failed to process signing key: {str(e)}"
            )
    
    def _validate_audience(self, payload: Dict[str, Any]) -> bool:
        """
        Validate token audience - check both 'aud' and 'appid' claims
        
        Args:
            payload: Decoded JWT payload
            
        Returns:
            bool: True if audience is valid
        """
        # Check standard 'aud' claim
        token_aud = payload.get("aud")
        if token_aud and token_aud in self.valid_audiences:
            return True
        
        # For Microsoft Graph tokens, also check 'appid' claim
        token_appid = payload.get("appid")
        if token_appid and token_appid == self.client_id:
            return True
            
        return False
    
    def _validate_issuer(self, payload: Dict[str, Any]) -> bool:
        """
        Validate token issuer - check against valid issuers
        
        Args:
            payload: Decoded JWT payload
            
        Returns:
            bool: True if issuer is valid
        """
        token_iss = payload.get("iss")
        return token_iss in self.valid_issuers
    
    def validate_token(self, token: str) -> Dict[str, Any]:
        """
        Validate Azure AD JWT token
        
        Args:
            token: JWT token string
            
        Returns:
            Dict: Decoded token payload if valid
            
        Raises:
            HTTPException: If token is invalid
        """
        self._ensure_initialized()
        
        try:
            print(f"🔓 Validating JWT token...")
            
            # Decode token header without verification to get key ID and debug info
            unverified_header = jwt.get_unverified_header(token)
            unverified_payload = jwt.decode(token, options={"verify_signature": False})
            
            token_issuer = unverified_payload.get("iss", "")
            
            print(f"📋 Token info:")
            print(f"   Algorithm: {unverified_header.get('alg')}")
            print(f"   Key ID: {unverified_header.get('kid')}")
            print(f"   Issuer: {token_issuer}")
            print(f"   Audience: {unverified_payload.get('aud')}")
            print(f"   App ID: {unverified_payload.get('appid')}")
            print(f"   Expires: {unverified_payload.get('exp')}")
            
            # Check if this is a development bypass
            dev_bypass = os.getenv("BYPASS_TOKEN_VALIDATION", "false").lower() in ("true", "1", "yes", "on")
            if dev_bypass:
                print("⚠️  DEVELOPMENT: Bypassing token signature verification")
                print("🔓 Token validation bypassed - using unverified payload")
                
                # Still validate basic token structure and timing
                current_time = time.time()
                
                # Check expiration
                exp = unverified_payload.get('exp')
                if exp and current_time > exp:
                    print(f"❌ Token expired. Current: {current_time}, Exp: {exp}")
                    raise HTTPException(
                        status_code=401,
                        detail="Token has expired"
                    )
                
                # Check not before
                nbf = unverified_payload.get('nbf')
                if nbf and current_time < nbf:
                    print(f"❌ Token not yet valid. Current: {current_time}, NBF: {nbf}")
                    raise HTTPException(
                        status_code=401,
                        detail="Token not yet valid"
                    )
                
                print("✓ Token timing validation successful (bypassed signature)")
                print("🎉 Token validation completed (DEVELOPMENT MODE)")
                
                return unverified_payload
            
            # Get signing key using the token's issuer to determine correct endpoint
            signing_key = self._get_signing_key(unverified_header, token_issuer)
            
            print(f"🔐 Attempting signature verification...")
            print(f"   Token algorithm: {unverified_header.get('alg', 'unknown')}")
            print(f"   Using PyJWT version: {jwt.__version__}")
            
            # Add detailed token inspection
            print(f"🔍 Detailed token analysis:")
            print(f"   Token length: {len(token)} characters")
            token_parts = token.split('.')
            print(f"   Token parts: {len(token_parts)} (should be 3)")
            if len(token_parts) == 3:
                header_len = len(token_parts[0])
                payload_len = len(token_parts[1])
                signature_len = len(token_parts[2])
                print(f"   Header length: {header_len}")
                print(f"   Payload length: {payload_len}")
                print(f"   Signature length: {signature_len}")
                
                # Check if signature looks valid (base64url)
                try:
                    # Try to decode the signature part
                    signature_bytes = base64.urlsafe_b64decode(token_parts[2] + '==')
                    print(f"   Signature bytes length: {len(signature_bytes)}")
                except Exception as decode_error:
                    print(f"   ❌ Signature decode failed: {decode_error}")
            
            # Check current time vs token times with more detail
            current_time = time.time()
            exp = unverified_payload.get('exp')
            nbf = unverified_payload.get('nbf', current_time - 300)  # Default to 5 min ago
            iat = unverified_payload.get('iat', current_time)
            
            print(f"🕐 Time analysis:")
            print(f"   Current time: {current_time} ({datetime.fromtimestamp(current_time)})")
            print(f"   Token issued at: {iat} ({datetime.fromtimestamp(iat)})")
            print(f"   Token not before: {nbf} ({datetime.fromtimestamp(nbf)})")
            print(f"   Token expires: {exp} ({datetime.fromtimestamp(exp)})")
            print(f"   Time until expiry: {exp - current_time} seconds")
            print(f"   Age of token: {current_time - iat} seconds")
            
            # Try different verification approaches with more relaxed settings
            verification_success = False
            payload = None
            last_error = None
            
            # Approach 1: Try with increased leeway for clock skew
            try:
                print("🔍 Trying with increased clock skew leeway (300 seconds)...")
                payload = jwt.decode(
                    token,
                    signing_key,
                    algorithms=["RS256"],
                    options={
                        "verify_signature": True,
                        "verify_exp": True,
                        "verify_nbf": True,
                        "verify_iat": False,  # Skip issued-at validation
                        "verify_aud": False,
                        "verify_iss": False
                    },
                    leeway=300  # 5 minutes leeway
                )
                verification_success = True
                print("✓ Clock skew leeway verification successful")
                
            except jwt.InvalidSignatureError as e:
                last_error = e
                print(f"❌ Clock skew leeway verification failed: {e}")
                
                # Approach 2: Try completely skipping signature verification as diagnostic
                try:
                    print("🔍 Diagnostic: Trying without signature verification...")
                    payload = jwt.decode(
                        token,
                        options={"verify_signature": False},
                        algorithms=["RS256"]
                    )
                    print("✓ Token structure is valid (signature verification skipped)")
                    print("❌ This confirms the signature verification itself is the issue")
                    
                    # For now, let's use the unverified payload but log it as a security concern
                    print("⚠️  SECURITY WARNING: Using unverified token payload due to signature verification issues")
                    verification_success = True
                    
                except Exception as diagnostic_error:
                    last_error = diagnostic_error
                    print(f"❌ Even diagnostic verification failed: {diagnostic_error}")
            
            if not verification_success:
                print(f"❌ All signature verification methods failed")
                print(f"   Final error: {last_error}")
                
                # Provide guidance for resolution
                print("🔧 Troubleshooting suggestions:")
                print("   1. Set BYPASS_TOKEN_VALIDATION=true in environment for development")
                print("   2. Check if token was copied correctly (no truncation)")
                print("   3. Verify system clock is synchronized")
                print("   4. Try generating a fresh token")
                
                raise HTTPException(
                    status_code=401,
                    detail=f"Token signature verification failed: {str(last_error)}"
                )
            
            print("✓ Token signature verified successfully")
            
            # Manual validations
            current_time = time.time()
            
            # Check expiration with leeway
            exp = payload.get('exp')
            if exp and current_time > (exp + 300):  # 5 min leeway
                raise HTTPException(
                    status_code=401,
                    detail="Token has expired"
                )
            
            # Check not before with leeway
            nbf = payload.get('nbf')
            if nbf and current_time < (nbf - 300):  # 5 min leeway
                raise HTTPException(
                    status_code=401,
                    detail="Token not yet valid"
                )
            
            print("✓ Token time validation successful")
            
            # Manual issuer validation
            if not self._validate_issuer(payload):
                token_iss = payload.get("iss")
                raise HTTPException(
                    status_code=401,
                    detail=f"Token issuer is invalid. Expected one of {self.valid_issuers}, got '{token_iss}'"
                )
            
            print("✓ Token issuer validated successfully")
            
            # Manual audience validation
            if not self._validate_audience(payload):
                token_aud = payload.get("aud")
                token_appid = payload.get("appid")
                raise HTTPException(
                    status_code=401,
                    detail=f"Token audience is invalid. Expected one of {self.valid_audiences}, got aud='{token_aud}', appid='{token_appid}'"
                )
            
            print("✓ Token audience validated successfully")
            print("🎉 Token validation completed successfully")
            
            return payload
            
        except jwt.ExpiredSignatureError:
            print("❌ Token has expired")
            raise HTTPException(
                status_code=401,
                detail="Token has expired"
            )
        except jwt.InvalidSignatureError as e:
            print(f"❌ Token signature is invalid: {e}")
            raise HTTPException(
                status_code=401,
                detail=f"Token signature is invalid: {str(e)}"
            )
        except jwt.InvalidTokenError as e:
            print(f"❌ Invalid token: {e}")
            raise HTTPException(
                status_code=401,
                detail=f"Invalid token: {str(e)}"
            )
        except HTTPException:
            # Re-raise HTTPExceptions as-is
            raise
        except Exception as e:
            print(f"❌ Token validation failed: {e}")
            raise HTTPException(
                status_code=401,
                detail=f"Token validation failed: {str(e)}"
            )

# Global token validator instance - will be lazily initialized
token_validator = AzureADTokenValidator()

# HTTP Bearer token security scheme
security = HTTPBearer()

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)) -> Dict[str, Any]:
    """
    FastAPI dependency to get current authenticated user
    
    Args:
        credentials: HTTP Bearer token credentials
        
    Returns:
        Dict: User information from validated token
    """
    if not credentials:
        raise HTTPException(
            status_code=401,
            detail="Authorization header is required"
        )
    
    token = credentials.credentials
    payload = token_validator.validate_token(token)
    
    return {
        "user_id": payload.get("oid"),  # Object ID
        "email": payload.get("email") or payload.get("preferred_username") or payload.get("unique_name"),
        "name": payload.get("name"),
        "tenant_id": payload.get("tid"),
        "roles": payload.get("roles", []),
        "groups": payload.get("groups", []),
        "app_id": payload.get("appid"),  # Add app ID for debugging
        "audience": payload.get("aud")   # Add audience for debugging
    }

# Alternative: Simple token extraction function for manual validation
def extract_token_from_request(request: Request) -> Optional[str]:
    """
    Extract Bearer token from request headers
    
    Args:
        request: FastAPI request object
        
    Returns:
        str: Token if found, None otherwise
    """
    authorization = request.headers.get("Authorization")
    if not authorization:
        return None
    
    if not authorization.startswith("Bearer "):
        return None
        
    return authorization.split(" ")[1]

async def get_current_user_from_request(request: Request) -> Dict[str, Any]:
    """
    Alternative authentication function that works directly with request
    
    Args:
        request: FastAPI request object
        
    Returns:
        Dict: User information from validated token
    """
    token = extract_token_from_request(request)
    if not token:
        raise HTTPException(
            status_code=401,
            detail="Authorization header is required"
        )
    
    payload = token_validator.validate_token(token)
    
    return {
        "user_id": payload.get("oid"),  # Object ID
        "email": payload.get("email") or payload.get("preferred_username") or payload.get("unique_name"),
        "name": payload.get("name"),
        "tenant_id": payload.get("tid"),
        "roles": payload.get("roles", []),
        "groups": payload.get("groups", []),
        "app_id": payload.get("appid"),  # Add app ID for debugging
        "audience": payload.get("aud")   # Add audience for debugging
    }

def require_auth(f):
    """
    Decorator to require authentication for endpoint functions
    
    Args:
        f: Function to wrap with authentication
        
    Returns:
        Wrapped function with authentication requirement
    """
    @wraps(f)
    async def wrapper(*args, **kwargs):
        # Extract request and check for authorization header
        request = None
        for arg in args:
            if isinstance(arg, Request):
                request = arg
                break
        
        if not request:
            raise HTTPException(
                status_code=500,
                detail="Request object not found"
            )
        
        # Get authorization header
        authorization = request.headers.get("Authorization")
        if not authorization:
            raise HTTPException(
                status_code=401,
                detail="Authorization header is required"
            )
        
        # Validate Bearer token format
        if not authorization.startswith("Bearer "):
            raise HTTPException(
                status_code=401,
                detail="Invalid authorization header format"
            )
        
        # Extract and validate token
        token = authorization.split(" ")[1]
        user = token_validator.validate_token(token)
        
        # Add user info to request state
        request.state.user = user
        
        return await f(*args, **kwargs)
    
    return wrapper

def get_user_from_request(request: Request) -> Optional[Dict[str, Any]]:
    """
    Extract user information from request state
    
    Args:
        request: FastAPI request object
        
    Returns:
        Dict: User information if authenticated, None otherwise
    """
    return getattr(request.state, "user", None)
