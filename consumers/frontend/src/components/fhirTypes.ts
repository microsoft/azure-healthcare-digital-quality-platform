export interface FhirPatient {
  id?: string;
  mrn?: string;
  name?: string;
  gender?: string;
  birthDate?: string;
  age?: number;
}

export interface FhirEncounter {
  id?: string;
  status?: string;
  class?: string;
  type?: string;
  start?: string;
  end?: string;
}

export interface FhirCondition {
  id?: string;
  code?: string;
  codeValue?: string;
  clinicalStatus?: string;
  verificationStatus?: string;
  onset?: string;
}

export interface FhirObservation {
  id?: string;
  status?: string;
  code?: string;
  codeValue?: string;
  effectiveDateTime?: string;
  systolic?: number | null;
  diastolic?: number | null;
}

export interface FhirProcedure {
  id?: string;
  status?: string;
  code?: string;
  codeValue?: string;
  performedDateTime?: string;
}

export interface FhirCoverage {
  id?: string;
  status?: string;
  type?: string;
  payors?: string[];
  start?: string;
  end?: string;
}

export interface FhirView {
  hasFhirBundle?: boolean;
  resourceCounts?: Record<string, number>;
  patient?: FhirPatient;
  encounters?: FhirEncounter[];
  conditions?: FhirCondition[];
  observations?: FhirObservation[];
  procedures?: FhirProcedure[];
  coverages?: FhirCoverage[];
}
