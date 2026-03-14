"use client";

import { FormEvent, useDeferredValue, useEffect, useState, useTransition } from "react";

import { getHost } from "@/helpers/getHost";
import type {
  BorrowerCase,
  QualitativeCreditOfficerNotes,
  SecondaryResearchFinding,
} from "@/types/credit";


const LAST_CASE_STORAGE_KEY = "intelli_credit_last_case_id";

const noteFields: Array<{
  key:
    | "factory_operating_capacity"
    | "management_quality"
    | "governance_concerns"
    | "collateral_observations"
    | "site_visit_comments"
    | "additional_comments";
  label: string;
  placeholder: string;
}> = [
  {
    key: "factory_operating_capacity",
    label: "Factory operating capacity",
    placeholder: "Capacity utilization, bottlenecks, maintenance gaps, and production reliability.",
  },
  {
    key: "management_quality",
    label: "Management quality",
    placeholder: "Leadership depth, execution quality, responsiveness, and succession observations.",
  },
  {
    key: "governance_concerns",
    label: "Governance concerns",
    placeholder: "Any related-party issues, disclosure concerns, process gaps, or integrity questions.",
  },
  {
    key: "collateral_observations",
    label: "Collateral observations",
    placeholder: "Asset condition, title issues, insurance, accessibility, or realizability observations.",
  },
  {
    key: "site_visit_comments",
    label: "Site-visit comments",
    placeholder: "Plant condition, labour availability, inventory movement, and on-ground impressions.",
  },
  {
    key: "additional_comments",
    label: "Additional comments",
    placeholder: "Anything else the credit officer wants preserved in the dossier.",
  },
];

const emptyNotes: QualitativeCreditOfficerNotes = {
  factory_operating_capacity: "",
  management_quality: "",
  governance_concerns: "",
  collateral_observations: "",
  site_visit_comments: "",
  additional_comments: "",
};


async function readJson<T>(response: Response): Promise<T> {
  const text = await response.text();
  return text ? JSON.parse(text) : ({} as T);
}


function artifactHref(path?: string): string {
  if (!path) {
    return "#";
  }

  const host = getHost();
  const cleanPath = path.replace(/^\/+/, "");
  return host ? `${host}/${cleanPath}` : `/${cleanPath}`;
}


function statusTone(status: string): string {
  if (status === "ready") {
    return "bg-emerald-500/15 text-emerald-200 ring-1 ring-emerald-400/30";
  }
  if (status === "attention_required") {
    return "bg-amber-500/15 text-amber-100 ring-1 ring-amber-300/30";
  }
  if (status === "ingestion_in_progress") {
    return "bg-cyan-500/15 text-cyan-100 ring-1 ring-cyan-300/30";
  }
  return "bg-white/8 text-slate-100 ring-1 ring-white/10";
}


function researchTone(status: string): string {
  if (status === "available") {
    return "bg-emerald-400/15 text-emerald-100";
  }
  if (status === "partial") {
    return "bg-amber-300/15 text-amber-100";
  }
  return "bg-white/8 text-slate-300";
}


function topicLabel(topic: string): string {
  return topic
    .replace("mca_", "MCA ")
    .replaceAll("_", " ")
    .replace(/\b\w/g, (value) => value.toUpperCase());
}


function filledNotesCount(notes?: QualitativeCreditOfficerNotes | null): number {
  if (!notes) {
    return 0;
  }

  return noteFields.reduce((count, field) => {
    return notes[field.key] && String(notes[field.key]).trim() ? count + 1 : count;
  }, 0);
}


export default function CreditWorkflowShell() {
  const [borrowerName, setBorrowerName] = useState("");
  const [externalReference, setExternalReference] = useState("");
  const [selectedFiles, setSelectedFiles] = useState<File[]>([]);
  const [currentCase, setCurrentCase] = useState<BorrowerCase | null>(null);
  const [noteDraft, setNoteDraft] = useState<QualitativeCreditOfficerNotes>(emptyNotes);
  const [message, setMessage] = useState(
    "Create a borrower case, upload files, run ingestion, and then capture research evidence and due-diligence notes."
  );
  const [error, setError] = useState<string | null>(null);
  const [activeAction, setActiveAction] = useState<string | null>(null);
  const [isPending, startTransition] = useTransition();
  const deferredCase = useDeferredValue(currentCase);
  const borrowerLegalName =
    deferredCase?.dossier.borrower["legal_name"]?.toString() ?? "Pending";
  const researchStatus = deferredCase?.dossier.secondary_research.status ?? "unavailable";
  const noteCount = filledNotesCount(deferredCase?.dossier.qualitative_credit_officer_notes);

  useEffect(() => {
    const savedCaseId = window.localStorage.getItem(LAST_CASE_STORAGE_KEY);
    if (savedCaseId) {
      void refreshCase(savedCaseId, false).catch(() => {
        window.localStorage.removeItem(LAST_CASE_STORAGE_KEY);
      });
    }
  }, []);

  useEffect(() => {
    if (currentCase?.case_id) {
      window.localStorage.setItem(LAST_CASE_STORAGE_KEY, currentCase.case_id);
    }
  }, [currentCase]);

  useEffect(() => {
    setNoteDraft(currentCase?.dossier.qualitative_credit_officer_notes ?? emptyNotes);
  }, [currentCase?.case_id, currentCase?.dossier.qualitative_credit_officer_notes?.updated_at]);

  async function refreshCase(caseId: string, updateMessage = true) {
    setError(null);

    const response = await fetch(`/api/credit/cases/${caseId}`);
    const data = await readJson<{ case: BorrowerCase }>(response);

    if (!response.ok) {
      throw new Error("Could not load the borrower case.");
    }

    startTransition(() => {
      setCurrentCase(data.case);
      if (updateMessage) {
        setMessage(`Loaded ${data.case.borrower_name}.`);
      }
    });
  }

  async function handleCreateCase(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setActiveAction("create");

    try {
      const response = await fetch("/api/credit/cases", {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({
          borrower_name: borrowerName,
          external_reference: externalReference || null,
        }),
      });

      const data = await readJson<{ case: BorrowerCase }>(response);
      if (!response.ok) {
        throw new Error("Could not create the borrower case.");
      }

      startTransition(() => {
        setCurrentCase(data.case);
        setSelectedFiles([]);
        setNoteDraft(data.case.dossier.qualitative_credit_officer_notes ?? emptyNotes);
        setMessage(`Created case ${data.case.case_id}. Upload borrower files next.`);
      });
    } catch (createError) {
      setError(createError instanceof Error ? createError.message : "Case creation failed.");
    } finally {
      setActiveAction(null);
    }
  }

  async function handleUpload() {
    if (!currentCase || selectedFiles.length === 0) {
      return;
    }

    setError(null);
    setActiveAction("upload");

    try {
      const formData = new FormData();
      selectedFiles.forEach((file) => formData.append("files", file));

      const response = await fetch(`/api/credit/cases/${currentCase.case_id}/files`, {
        method: "POST",
        body: formData,
      });

      const data = await readJson<{ case: BorrowerCase }>(response);
      if (!response.ok) {
        throw new Error("File upload failed.");
      }

      startTransition(() => {
        setCurrentCase(data.case);
        setSelectedFiles([]);
        setMessage(`Uploaded ${data.case.uploaded_files.length} borrower file(s).`);
      });
    } catch (uploadError) {
      setError(uploadError instanceof Error ? uploadError.message : "File upload failed.");
    } finally {
      setActiveAction(null);
    }
  }

  async function handleIngest() {
    if (!currentCase) {
      return;
    }

    setError(null);
    setActiveAction("ingest");

    try {
      const response = await fetch(`/api/credit/cases/${currentCase.case_id}/ingest`, {
        method: "POST",
      });

      const data = await readJson<{ case: BorrowerCase }>(response);
      if (!response.ok) {
        throw new Error("Ingestion failed.");
      }

      startTransition(() => {
        setCurrentCase(data.case);
        setMessage("Borrower ingestion completed. The structured dossier is ready for research and note enrichment.");
      });
    } catch (ingestError) {
      setError(ingestError instanceof Error ? ingestError.message : "Ingestion failed.");
    } finally {
      setActiveAction(null);
    }
  }

  async function handleSaveNotes() {
    if (!currentCase) {
      return;
    }

    setError(null);
    setActiveAction("notes");

    try {
      const response = await fetch(`/api/credit/cases/${currentCase.case_id}/notes`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify(noteDraft),
      });

      const data = await readJson<{ case: BorrowerCase }>(response);
      if (!response.ok) {
        throw new Error("Could not save qualitative notes.");
      }

      startTransition(() => {
        setCurrentCase(data.case);
        setNoteDraft(data.case.dossier.qualitative_credit_officer_notes ?? emptyNotes);
        setMessage("Qualitative credit officer notes saved into the borrower dossier.");
      });
    } catch (notesError) {
      setError(notesError instanceof Error ? notesError.message : "Could not save notes.");
    } finally {
      setActiveAction(null);
    }
  }

  async function handleRunResearch() {
    if (!currentCase) {
      return;
    }

    setError(null);
    setActiveAction("research");

    try {
      const response = await fetch(`/api/credit/cases/${currentCase.case_id}/research`, {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
        },
        body: JSON.stringify({}),
      });

      const data = await readJson<{ case: BorrowerCase }>(response);
      if (!response.ok) {
        throw new Error("Secondary research failed.");
      }

      startTransition(() => {
        setCurrentCase(data.case);
        setMessage(
          `Secondary research completed with status ${data.case.dossier.secondary_research.status}.`
        );
      });
    } catch (researchError) {
      setError(
        researchError instanceof Error ? researchError.message : "Secondary research failed."
      );
    } finally {
      setActiveAction(null);
    }
  }

  function updateNoteField(
    key: keyof QualitativeCreditOfficerNotes,
    value: string
  ) {
    setNoteDraft((current) => ({
      ...current,
      [key]: value,
    }));
  }

  function renderResearchFinding(finding: SecondaryResearchFinding) {
    return (
      <div
        key={finding.topic}
        className="rounded-2xl border border-white/8 bg-slate-950/70 px-4 py-4"
      >
        <div className="flex flex-wrap items-center gap-2">
          <p className="text-sm font-medium text-white">{topicLabel(finding.topic)}</p>
          <span className={`rounded-full px-2 py-0.5 text-xs ${researchTone(finding.status)}`}>
            {finding.status}
          </span>
        </div>
        {finding.summary ? (
          <p className="mt-2 text-sm text-slate-300">{finding.summary}</p>
        ) : null}
        {finding.message ? (
          <p className="mt-2 text-sm text-amber-100">{finding.message}</p>
        ) : null}
        {finding.risk_flags.length > 0 ? (
          <div className="mt-3 flex flex-wrap gap-2">
            {finding.risk_flags.map((flag) => (
              <span
                key={`${finding.topic}-${flag}`}
                className="rounded-full bg-rose-400/10 px-2 py-0.5 text-xs text-rose-100"
              >
                {flag}
              </span>
            ))}
          </div>
        ) : null}
        {finding.evidence.length > 0 ? (
          <div className="mt-4 space-y-3">
            {finding.evidence.map((evidence) => (
              <div
                key={evidence.evidence_id}
                className="rounded-2xl border border-white/8 bg-white/5 px-4 py-3"
              >
                <div className="flex flex-wrap items-center gap-2">
                  <p className="text-sm font-medium text-white">{evidence.title}</p>
                  <span className="rounded-full bg-white/8 px-2 py-0.5 text-xs text-slate-300">
                    {evidence.source_type}
                  </span>
                </div>
                <p className="mt-2 text-sm text-slate-300">{evidence.summary}</p>
                {evidence.extracted_risk_flags.length > 0 ? (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {evidence.extracted_risk_flags.map((flag) => (
                      <span
                        key={`${evidence.evidence_id}-${flag}`}
                        className="rounded-full bg-amber-300/15 px-2 py-0.5 text-xs text-amber-100"
                      >
                        {flag}
                      </span>
                    ))}
                  </div>
                ) : null}
                {evidence.source_url ? (
                  <a
                    href={evidence.source_url}
                    target="_blank"
                    rel="noreferrer"
                    className="mt-3 inline-flex text-xs text-cyan-200 transition hover:text-cyan-100"
                  >
                    Open source
                  </a>
                ) : null}
              </div>
            ))}
          </div>
        ) : null}
      </div>
    );
  }

  return (
    <main className="min-h-screen bg-transparent px-4 py-8 text-white sm:px-6 lg:px-10">
      <div className="mx-auto flex w-full max-w-7xl flex-col gap-8">
        <section className="overflow-hidden rounded-[32px] border border-white/10 bg-slate-950/70 shadow-[0_30px_120px_rgba(8,15,30,0.45)] backdrop-blur">
          <div className="grid gap-8 px-6 py-8 lg:grid-cols-[1.2fr_0.8fr] lg:px-10 lg:py-10">
            <div className="space-y-6">
              <div className="inline-flex items-center gap-2 rounded-full border border-cyan-400/20 bg-cyan-400/10 px-3 py-1 text-xs uppercase tracking-[0.24em] text-cyan-100">
                Intelli-Credit
                <span className="rounded-full bg-amber-300/20 px-2 py-0.5 text-[10px] text-amber-100">
                  Live workflow
                </span>
              </div>
              <div className="max-w-3xl space-y-3">
                <h1 className="text-4xl font-semibold tracking-tight text-white sm:text-5xl">
                  Borrower intake now carries evidence gathering and on-ground diligence.
                </h1>
                <p className="max-w-2xl text-base text-slate-300 sm:text-lg">
                  Borrower documents are normalized into structured financial signals,
                  then enriched with secondary research evidence, source links, risk
                  flags, and qualitative credit officer notes directly in the dossier.
                </p>
              </div>

              <form
                onSubmit={handleCreateCase}
                className="grid gap-4 rounded-[28px] border border-white/10 bg-white/5 p-5 sm:grid-cols-2"
              >
                <label className="space-y-2 sm:col-span-2">
                  <span className="text-sm font-medium text-slate-200">Borrower name</span>
                  <input
                    value={borrowerName}
                    onChange={(event) => setBorrowerName(event.target.value)}
                    placeholder="Acme Manufacturing Pvt Ltd"
                    className="w-full rounded-2xl border border-white/10 bg-slate-950/70 px-4 py-3 text-white outline-none transition focus:border-cyan-300/60"
                    required
                  />
                </label>
                <label className="space-y-2">
                  <span className="text-sm font-medium text-slate-200">External reference</span>
                  <input
                    value={externalReference}
                    onChange={(event) => setExternalReference(event.target.value)}
                    placeholder="HT-2026-001"
                    className="w-full rounded-2xl border border-white/10 bg-slate-950/70 px-4 py-3 text-white outline-none transition focus:border-cyan-300/60"
                  />
                </label>
                <div className="flex items-end">
                  <button
                    type="submit"
                    disabled={activeAction === "create" || isPending || !borrowerName.trim()}
                    className="w-full rounded-2xl bg-gradient-to-r from-cyan-400 to-teal-300 px-4 py-3 font-medium text-slate-950 transition hover:brightness-110 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {activeAction === "create" ? "Creating..." : "Create case"}
                  </button>
                </div>
              </form>

              <div className="grid gap-4 rounded-[28px] border border-white/10 bg-slate-900/60 p-5 lg:grid-cols-[1fr_auto_auto]">
                <label className="space-y-2">
                  <span className="text-sm font-medium text-slate-200">Borrower files</span>
                  <input
                    type="file"
                    multiple
                    onChange={(event) =>
                      setSelectedFiles(Array.from(event.target.files ?? []))
                    }
                    className="block w-full rounded-2xl border border-dashed border-white/20 bg-slate-950/60 px-4 py-3 text-sm text-slate-300 file:mr-4 file:rounded-full file:border-0 file:bg-white file:px-3 file:py-2 file:text-sm file:font-medium file:text-slate-950"
                  />
                </label>
                <button
                  type="button"
                  onClick={handleUpload}
                  disabled={
                    !currentCase || selectedFiles.length === 0 || activeAction === "upload" || isPending
                  }
                  className="rounded-2xl border border-white/10 bg-white/10 px-5 py-3 text-sm font-medium text-white transition hover:bg-white/15 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {activeAction === "upload" ? "Uploading..." : "Upload files"}
                </button>
                <button
                  type="button"
                  onClick={handleIngest}
                  disabled={!currentCase || activeAction === "ingest" || isPending}
                  className="rounded-2xl border border-emerald-300/30 bg-emerald-400/15 px-5 py-3 text-sm font-medium text-emerald-100 transition hover:bg-emerald-400/20 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {activeAction === "ingest" ? "Running..." : "Trigger ingestion"}
                </button>
              </div>

              <div className="grid gap-4 rounded-[28px] border border-white/10 bg-[linear-gradient(135deg,rgba(10,19,35,0.92),rgba(14,38,48,0.88))] p-5 lg:grid-cols-[1fr_auto]">
                <div className="space-y-2">
                  <p className="text-xs uppercase tracking-[0.24em] text-slate-400">
                    Research enrichment
                  </p>
                  <h2 className="text-2xl font-semibold text-white">
                    Run secondary research and merge public-web evidence.
                  </h2>
                  <p className="max-w-2xl text-sm text-slate-300">
                    The provider reuses local `gpt-researcher` capability when configured, and
                    gracefully returns structured unavailable results when keys or retrievers are
                    not ready in this environment.
                  </p>
                </div>
                <button
                  type="button"
                  onClick={handleRunResearch}
                  disabled={!currentCase || activeAction === "research" || isPending}
                  className="rounded-2xl border border-cyan-300/30 bg-cyan-400/15 px-5 py-3 text-sm font-medium text-cyan-100 transition hover:bg-cyan-400/20 disabled:cursor-not-allowed disabled:opacity-60"
                >
                  {activeAction === "research" ? "Researching..." : "Run secondary research"}
                </button>
              </div>
            </div>

            <aside className="space-y-4 rounded-[28px] border border-white/10 bg-[linear-gradient(180deg,rgba(17,25,40,0.92),rgba(7,13,24,0.92))] p-5">
              <div className="flex items-start justify-between gap-4">
                <div>
                  <p className="text-xs uppercase tracking-[0.24em] text-slate-400">
                    Current case
                  </p>
                  <h2 className="mt-2 text-2xl font-semibold text-white">
                    {currentCase?.borrower_name ?? "No case yet"}
                  </h2>
                </div>
                <span
                  className={`rounded-full px-3 py-1 text-xs font-medium capitalize ${statusTone(
                    currentCase?.status ?? "created"
                  )}`}
                >
                  {currentCase?.status?.replaceAll("_", " ") ?? "created"}
                </span>
              </div>

              <div className="space-y-3 rounded-3xl border border-white/8 bg-white/5 p-4 text-sm text-slate-300">
                <p>{message}</p>
                {error ? <p className="text-rose-200">{error}</p> : null}
                {currentCase ? (
                  <div className="grid gap-2 text-slate-300">
                    <div className="flex items-center justify-between gap-3">
                      <span>Case ID</span>
                      <code className="text-xs text-cyan-100">{currentCase.case_id}</code>
                    </div>
                    <div className="flex items-center justify-between gap-3">
                      <span>Uploaded files</span>
                      <span>{currentCase.uploaded_files.length}</span>
                    </div>
                    <div className="flex items-center justify-between gap-3">
                      <span>Ingested docs</span>
                      <span>{currentCase.dossier.documents.length}</span>
                    </div>
                    <div className="flex items-center justify-between gap-3">
                      <span>Research status</span>
                      <span className="capitalize">{researchStatus.replaceAll("_", " ")}</span>
                    </div>
                    <div className="flex items-center justify-between gap-3">
                      <span>Evidence items</span>
                      <span>{currentCase.dossier.secondary_research.evidence.length}</span>
                    </div>
                    <div className="flex items-center justify-between gap-3">
                      <span>Qualitative notes</span>
                      <span>{noteCount}</span>
                    </div>
                    <div className="flex items-center justify-between gap-3">
                      <span>Risk flags</span>
                      <span>{currentCase.dossier.risk_flags.length}</span>
                    </div>
                  </div>
                ) : null}
              </div>

              <div className="space-y-3">
                <div className="flex items-center justify-between">
                  <h3 className="text-sm font-medium uppercase tracking-[0.2em] text-slate-400">
                    Artifacts
                  </h3>
                  {currentCase ? (
                    <button
                      type="button"
                      onClick={() => void refreshCase(currentCase.case_id)}
                      className="text-xs text-cyan-200 transition hover:text-cyan-100"
                    >
                      Refresh
                    </button>
                  ) : null}
                </div>
                <div className="flex flex-wrap gap-3">
                  <a
                    href={artifactHref(currentCase?.artifacts?.dossier_json)}
                    target="_blank"
                    rel="noreferrer"
                    className="rounded-full border border-white/10 bg-white/8 px-4 py-2 text-sm text-white transition hover:bg-white/12"
                  >
                    Dossier JSON
                  </a>
                  <a
                    href={artifactHref(currentCase?.artifacts?.dossier_markdown)}
                    target="_blank"
                    rel="noreferrer"
                    className="rounded-full border border-white/10 bg-white/8 px-4 py-2 text-sm text-white transition hover:bg-white/12"
                  >
                    Dossier Markdown
                  </a>
                </div>
              </div>

              <div className="space-y-3">
                <h3 className="text-sm font-medium uppercase tracking-[0.2em] text-slate-400">
                  Uploaded files
                </h3>
                <div className="space-y-2">
                  {(currentCase?.uploaded_files ?? []).length === 0 ? (
                    <p className="text-sm text-slate-400">
                      Upload PDFs, office docs, text files, or images to start the intake flow.
                    </p>
                  ) : (
                    currentCase?.uploaded_files.map((file) => (
                      <div
                        key={file.file_id}
                        className="rounded-2xl border border-white/8 bg-white/5 px-4 py-3 text-sm text-slate-200"
                      >
                        <div className="flex items-center justify-between gap-3">
                          <span className="truncate">{file.original_filename}</span>
                          <span className="rounded-full bg-white/8 px-2 py-0.5 text-xs capitalize text-slate-300">
                            {file.adapter_hint}
                          </span>
                        </div>
                        <p className="mt-1 text-xs text-slate-400">
                          {file.status.replaceAll("_", " ")} - {(file.size_bytes / 1024).toFixed(1)} KB
                        </p>
                      </div>
                    ))
                  )}
                </div>
              </div>
            </aside>
          </div>
        </section>

        <section className="grid gap-6 xl:grid-cols-[0.95fr_1.05fr]">
          <div className="space-y-6 rounded-[28px] border border-white/10 bg-slate-950/70 p-5 backdrop-blur">
            <div className="flex items-center justify-between gap-4">
              <div>
                <p className="text-xs uppercase tracking-[0.24em] text-slate-400">
                  Structured dossier
                </p>
                <h2 className="mt-2 text-2xl font-semibold text-white">
                  Borrower evidence workspace
                </h2>
              </div>
              <div className="rounded-full bg-white/8 px-3 py-1 text-xs text-slate-200">
                {deferredCase?.dossier?.schema_version ?? "Not generated yet"}
              </div>
            </div>

            <div className="grid gap-4">
              <div className="rounded-3xl border border-white/8 bg-white/5 p-4">
                <p className="text-xs uppercase tracking-[0.2em] text-slate-400">Borrower</p>
                <p className="mt-2 text-lg font-medium text-white">{borrowerLegalName}</p>
              </div>

              <div className="rounded-3xl border border-white/8 bg-white/5 p-4">
                <p className="text-xs uppercase tracking-[0.2em] text-slate-400">Open items</p>
                <div className="mt-3 space-y-3">
                  {(deferredCase?.dossier.open_items ?? []).length === 0 ? (
                    <p className="text-sm text-slate-400">No open items yet.</p>
                  ) : (
                    deferredCase?.dossier.open_items.map((item) => (
                      <div
                        key={item.code}
                        className="rounded-2xl border border-white/8 bg-slate-950/70 px-4 py-3"
                      >
                        <p className="text-sm font-medium text-white">{item.title}</p>
                        <p className="mt-1 text-sm text-slate-400">{item.description}</p>
                      </div>
                    ))
                  )}
                </div>
              </div>

              <div className="rounded-3xl border border-white/8 bg-white/5 p-4">
                <div className="flex items-center justify-between gap-3">
                  <div>
                    <p className="text-xs uppercase tracking-[0.2em] text-slate-400">
                      Qualitative credit officer notes
                    </p>
                    <h3 className="mt-2 text-lg font-semibold text-white">
                      Primary due-diligence capture
                    </h3>
                  </div>
                  <button
                    type="button"
                    onClick={handleSaveNotes}
                    disabled={!currentCase || activeAction === "notes" || isPending}
                    className="rounded-2xl border border-white/10 bg-white/10 px-4 py-2 text-sm font-medium text-white transition hover:bg-white/15 disabled:cursor-not-allowed disabled:opacity-60"
                  >
                    {activeAction === "notes" ? "Saving..." : "Save notes"}
                  </button>
                </div>
                <div className="mt-4 grid gap-4">
                  {noteFields.map((field) => (
                    <label key={field.key} className="space-y-2">
                      <span className="text-sm font-medium text-slate-200">{field.label}</span>
                      <textarea
                        value={String(noteDraft[field.key] ?? "")}
                        onChange={(event) => updateNoteField(field.key, event.target.value)}
                        rows={3}
                        placeholder={field.placeholder}
                        className="w-full rounded-2xl border border-white/10 bg-slate-950/70 px-4 py-3 text-sm text-white outline-none transition focus:border-cyan-300/60"
                      />
                    </label>
                  ))}
                </div>
              </div>

              <div className="rounded-3xl border border-white/8 bg-white/5 p-4">
                <div className="flex flex-wrap items-center justify-between gap-3">
                  <div>
                    <p className="text-xs uppercase tracking-[0.2em] text-slate-400">
                      Secondary research
                    </p>
                    <h3 className="mt-2 text-lg font-semibold text-white">
                      Evidence, source URLs, and extracted flags
                    </h3>
                  </div>
                  <span className={`rounded-full px-3 py-1 text-xs ${researchTone(researchStatus)}`}>
                    {researchStatus}
                  </span>
                </div>

                <div className="mt-4 space-y-4">
                  {deferredCase?.dossier.secondary_research.coverage_note ? (
                    <div className="rounded-2xl border border-white/8 bg-slate-950/70 px-4 py-3">
                      <p className="text-sm text-slate-300">
                        {deferredCase.dossier.secondary_research.coverage_note}
                      </p>
                    </div>
                  ) : null}

                  {deferredCase?.dossier.secondary_research.message ? (
                    <p className="text-sm text-amber-100">
                      {deferredCase.dossier.secondary_research.message}
                    </p>
                  ) : null}

                  {(deferredCase?.dossier.secondary_research.findings ?? []).length === 0 ? (
                    <p className="text-sm text-slate-400">
                      Run secondary research to capture company, promoter, litigation, sector,
                      and MCA-style public-web evidence.
                    </p>
                  ) : (
                    deferredCase?.dossier.secondary_research.findings.map((finding) =>
                      renderResearchFinding(finding)
                    )
                  )}

                  {(deferredCase?.dossier.secondary_research.source_urls ?? []).length > 0 ? (
                    <div className="rounded-2xl border border-white/8 bg-slate-950/70 px-4 py-4">
                      <p className="text-xs uppercase tracking-[0.2em] text-slate-400">
                        Source URLs
                      </p>
                      <div className="mt-3 flex flex-col gap-2">
                        {deferredCase?.dossier.secondary_research.source_urls.map((url) => (
                          <a
                            key={url}
                            href={url}
                            target="_blank"
                            rel="noreferrer"
                            className="truncate text-sm text-cyan-200 transition hover:text-cyan-100"
                          >
                            {url}
                          </a>
                        ))}
                      </div>
                    </div>
                  ) : null}
                </div>
              </div>

              <div className="rounded-3xl border border-white/8 bg-white/5 p-4">
                <p className="text-xs uppercase tracking-[0.2em] text-slate-400">Documents</p>
                <div className="mt-3 space-y-3">
                  {(deferredCase?.dossier.documents ?? []).length === 0 ? (
                    <p className="text-sm text-slate-400">
                      Ingestion results will land here as normalized document entries.
                    </p>
                  ) : (
                    deferredCase?.dossier.documents.map((document) => (
                      <div
                        key={document.document_id}
                        className="rounded-2xl border border-white/8 bg-slate-950/70 px-4 py-4"
                      >
                        <div className="flex flex-wrap items-center gap-2">
                          <p className="text-sm font-medium text-white">{document.filename}</p>
                          <span className="rounded-full bg-cyan-400/10 px-2 py-0.5 text-xs text-cyan-100">
                            {document.adapter}
                          </span>
                          <span className="rounded-full bg-white/8 px-2 py-0.5 text-xs text-slate-300">
                            {document.category}
                          </span>
                          {document.placeholder ? (
                            <span className="rounded-full bg-amber-300/15 px-2 py-0.5 text-xs text-amber-100">
                              placeholder
                            </span>
                          ) : null}
                        </div>
                        <p className="mt-2 text-sm text-slate-300">
                          {String(
                            document.extracted_fields["text_excerpt"] ??
                              "No preview available"
                          )}
                        </p>
                        {document.warnings.length > 0 ? (
                          <div className="mt-3 space-y-1 text-xs text-amber-100">
                            {document.warnings.map((warning, index) => (
                              <p key={`${document.document_id}-${index}`}>{warning}</p>
                            ))}
                          </div>
                        ) : null}
                      </div>
                    ))
                  )}
                </div>
              </div>
            </div>
          </div>

          <div className="space-y-6">
            <div className="rounded-[28px] border border-white/10 bg-slate-950/70 p-5 backdrop-blur">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <p className="text-xs uppercase tracking-[0.24em] text-slate-400">
                    Risk flags
                  </p>
                  <h2 className="mt-2 text-2xl font-semibold text-white">
                    Merged structured, research, and note signals
                  </h2>
                </div>
                <div className="rounded-full bg-white/8 px-3 py-1 text-xs text-slate-200">
                  {(deferredCase?.dossier.risk_flags ?? []).length} total
                </div>
              </div>
              <div className="mt-5 space-y-3">
                {(deferredCase?.dossier.risk_flags ?? []).length === 0 ? (
                  <p className="text-sm text-slate-400">
                    Structured parsers, secondary research, and qualitative notes have not
                    produced any explicit risk flags yet.
                  </p>
                ) : (
                  deferredCase?.dossier.risk_flags.map((flag, index) => (
                    <div
                      key={`${flag.category?.toString() ?? "flag"}-${index}`}
                      className="rounded-2xl border border-white/8 bg-white/5 px-4 py-3"
                    >
                      <div className="flex flex-wrap items-center gap-2">
                        <span className="rounded-full bg-rose-400/10 px-2 py-0.5 text-xs text-rose-100">
                          {String(flag.severity ?? "unknown")}
                        </span>
                        <span className="rounded-full bg-white/8 px-2 py-0.5 text-xs text-slate-300">
                          {String(flag.category ?? "general")}
                        </span>
                        {flag.topic ? (
                          <span className="rounded-full bg-cyan-400/10 px-2 py-0.5 text-xs text-cyan-100">
                            {String(flag.topic).replaceAll("_", " ")}
                          </span>
                        ) : null}
                      </div>
                      <p className="mt-2 text-sm text-slate-300">
                        {String(flag.description ?? "No description available")}
                      </p>
                    </div>
                  ))
                )}
              </div>
            </div>

            <div className="rounded-[28px] border border-white/10 bg-slate-950/70 p-5 backdrop-blur">
              <div className="flex items-center justify-between gap-4">
                <div>
                  <p className="text-xs uppercase tracking-[0.24em] text-slate-400">Raw payload</p>
                  <h2 className="mt-2 text-2xl font-semibold text-white">Canonical dossier JSON</h2>
                </div>
                <div className="rounded-full bg-white/8 px-3 py-1 text-xs text-slate-200">
                  read-only
                </div>
              </div>
              <pre className="mt-5 max-h-[980px] overflow-auto rounded-[24px] border border-white/8 bg-slate-900/80 p-4 text-xs leading-6 text-cyan-100">
                {deferredCase
                  ? JSON.stringify(deferredCase.dossier, null, 2)
                  : "Create a case to see the normalized dossier payload."}
              </pre>
            </div>
          </div>
        </section>
      </div>
    </main>
  );
}
