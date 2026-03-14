export interface CreditFileRecord {
  file_id: string;
  filename: string;
  original_filename: string;
  extension: string;
  media_type: string;
  size_bytes: number;
  uploaded_at: string;
  storage_path: string;
  adapter_hint: string;
  status: string;
  document_id?: string | null;
  warnings: string[];
}

export interface CreditDocument {
  document_id: string;
  file_id: string;
  filename: string;
  category: string;
  adapter: string;
  status: string;
  placeholder: boolean;
  extracted_text: string;
  extracted_fields: Record<string, unknown>;
  tables: Array<Record<string, unknown>>;
  pages: Array<Record<string, unknown>>;
  warnings: string[];
  metadata: Record<string, unknown>;
}

export interface CreditOpenItem {
  code: string;
  title: string;
  description: string;
  severity: string;
  status: string;
  related_document_ids: string[];
}

export interface SecondaryResearchEvidence {
  evidence_id: string;
  topic: string;
  title: string;
  summary: string;
  source_url?: string | null;
  source_title?: string | null;
  source_type: string;
  provider?: string | null;
  confidence?: string | null;
  observed_at?: string | null;
  extracted_risk_flags: string[];
}

export interface SecondaryResearchFinding {
  topic: string;
  status: string;
  summary?: string | null;
  evidence: SecondaryResearchEvidence[];
  source_urls: string[];
  risk_flags: string[];
  caveats: string[];
  message?: string | null;
}

export interface SecondaryResearchSection {
  status: string;
  provider: string;
  query?: string | null;
  executed_at?: string | null;
  evidence: SecondaryResearchEvidence[];
  findings: SecondaryResearchFinding[];
  source_urls: string[];
  extracted_risk_flags: string[];
  coverage_note?: string | null;
  message?: string | null;
}

export interface QualitativeCreditOfficerNotes {
  factory_operating_capacity?: string | null;
  management_quality?: string | null;
  governance_concerns?: string | null;
  collateral_observations?: string | null;
  site_visit_comments?: string | null;
  additional_comments?: string | null;
  updated_at?: string | null;
  updated_by?: string | null;
}

export interface BorrowerDossier {
  schema_version: string;
  case_id: string;
  borrower: Record<string, unknown>;
  credit_request: Record<string, unknown>;
  financial_snapshot: Record<string, unknown>;
  documents: CreditDocument[];
  structured_documents: Array<Record<string, unknown>>;
  credit_features?: Record<string, unknown> | null;
  secondary_research: SecondaryResearchSection;
  qualitative_credit_officer_notes: QualitativeCreditOfficerNotes;
  risk_flags: Array<Record<string, unknown>>;
  open_items: CreditOpenItem[];
  ingestion_summary: Record<string, unknown>;
  notes: string[];
}

export interface BorrowerCase {
  case_id: string;
  borrower_name: string;
  external_reference?: string | null;
  status: string;
  created_at: string;
  updated_at: string;
  last_ingested_at?: string | null;
  uploaded_files: CreditFileRecord[];
  dossier: BorrowerDossier;
  artifacts: Record<string, string>;
  timeline: Array<Record<string, unknown>>;
}
