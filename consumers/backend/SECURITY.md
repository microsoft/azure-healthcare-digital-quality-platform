<!-- BEGIN MICROSOFT SECURITY.MD V0.0.5 BLOCK -->

## Security

Microsoft takes the security of our software products and services seriously, which includes all source code repositories managed through our GitHub organizations, which include [Microsoft](https://github.com/Microsoft), [Azure](https://github.com/Azure), [DotNet](https://github.com/dotnet), [AspNet](https://github.com/aspnet), [Xamarin](https://github.com/xamarin), and [our GitHub organizations](https://opensource.microsoft.com/).

If you believe you have found a security vulnerability in any Microsoft-owned repository that meets [Microsoft's definition of a security vulnerability](<https://docs.microsoft.com/previous-versions/tn-archive/cc751383(v=technet.10)>), please report it to us as described below.

## Reporting Security Issues

**Please do not report security vulnerabilities through public GitHub issues.**

Instead, please report them to the Microsoft Security Response Center (MSRC) at [https://msrc.microsoft.com/create-report](https://msrc.microsoft.com/create-report).

If you prefer to submit without logging in, send email to [secure@microsoft.com](mailto:secure@microsoft.com). If possible, encrypt your message with our PGP key; please download it from the [Microsoft Security Response Center PGP Key page](https://www.microsoft.com/msrc/pgp-key-msrc).

You should receive a response within 24 hours. If for some reason you do not, please follow up via email to ensure we received your original message. Additional information can be found at [microsoft.com/msrc](https://www.microsoft.com/msrc).

Please include the requested information listed below (as much as you can provide) to help us better understand the nature and scope of the possible issue:

- Type of issue (e.g. buffer overflow, SQL injection, cross-site scripting, etc.)
- Full paths of source file(s) related to the manifestation of the issue
- The location of the affected source code (tag/branch/commit or direct URL)
- Any special configuration required to reproduce the issue
- Step-by-step instructions to reproduce the issue
- Proof-of-concept or exploit code (if possible)
- Impact of the issue, including how an attacker might exploit the issue

This information will help us triage your report more quickly.

If you are reporting for a bug bounty, more complete reports can contribute to a higher bounty award. Please visit our [Microsoft Bug Bounty Program](https://microsoft.com/msrc/bounty) page for more details about our active programs.

## Preferred Languages

We prefer all communications to be in English.

## Policy

Microsoft follows the principle of [Coordinated Vulnerability Disclosure](https://www.microsoft.com/msrc/cvd).

### Backend Security Model

This digital quality backend API implements enterprise-grade security controls:

#### SSL/TLS Certificate Security
- **Certificate Validation**: All outbound HTTPS requests validate certificates
- **Certificate Management**: Proper certificate chain validation for Azure service communications
- **Development Guidance**: Clear separation between development and production certificate handling
- **Certificate Rotation**: Support for automated certificate rotation in production environments

#### API Security
- **Azure AD Token Validation**: All API endpoints validate JWT tokens from Azure AD
- **Certificate-based Authentication**: Support for client certificate authentication
- **HTTPS Enforcement**: All communications require SSL/TLS encryption
- **Security Headers**: Proper security headers implemented for certificate and connection security

#### Network Security
- **Certificate Pinning**: Implemented for critical Azure service connections
- **TLS Configuration**: Modern TLS versions required (TLS 1.2+)
- **Certificate Authority Validation**: Only trusted CAs accepted for certificate validation

#### Development vs Production
- **Development**: Certificate issues logged with appropriate guidance
- **Production**: Strict certificate validation with no bypasses
- **Monitoring**: Certificate expiration and validation failures monitored and alerted

<!-- END MICROSOFT SECURITY.MD BLOCK -->