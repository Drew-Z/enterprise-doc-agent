import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type FormEvent } from "react";
import {
  Building2,
  LoaderCircle,
  LockKeyhole,
  ShieldCheck,
  Trash2,
  User,
  UserPlus,
  Users,
  X,
} from "lucide-react";

import type { DocumentAccessMode, DocumentInventoryItem } from "../agent/api/schemas";
import { formatApiError } from "../api/errorDisplay";
import { useT } from "../i18n";
import {
  createDocumentGrant,
  deleteDocumentGrant,
  fetchDocumentAccess,
  fetchDocumentGrants,
  updateDocumentAccess,
} from "./documentsApi";

interface DocumentAccessDrawerProps {
  document: DocumentInventoryItem;
  token: string;
  onClose: () => void;
  onUpdated: () => void;
}

type GrantKind = "user" | "role";

function errorMessage(error: unknown, fallback: string, requestIdLabel: string): string {
  return formatApiError(error, fallback, requestIdLabel);
}

export function DocumentAccessDrawer({
  document,
  token,
  onClose,
  onUpdated,
}: DocumentAccessDrawerProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const [grantKind, setGrantKind] = useState<GrantKind>("user");
  const [userId, setUserId] = useState("");
  const [role, setRole] = useState<"owner" | "member">("member");
  const accessKey = ["document-access", document.documentId] as const;
  const grantsKey = ["document-grants", document.documentId] as const;
  const access = useQuery({
    queryKey: accessKey,
    queryFn: ({ signal }) => fetchDocumentAccess(token, document.documentId, signal),
    initialData: {
      documentId: document.documentId,
      accessMode: document.accessMode,
      canManage: document.canManage,
    },
    retry: false,
  });
  const grants = useQuery({
    queryKey: grantsKey,
    queryFn: ({ signal }) => fetchDocumentGrants(token, document.documentId, signal),
    retry: false,
  });
  const updateMode = useMutation({
    mutationFn: (accessMode: DocumentAccessMode) =>
      updateDocumentAccess(token, document.documentId, accessMode),
    onSuccess: async (result) => {
      queryClient.setQueryData(accessKey, result);
      await queryClient.invalidateQueries({ queryKey: ["document-inventory"] });
      onUpdated();
    },
  });
  const addGrant = useMutation({
    mutationFn: () => createDocumentGrant(
      token,
      document.documentId,
      grantKind === "user"
        ? { granteeUserId: userId.trim() }
        : { granteeRole: role },
    ),
    onSuccess: async () => {
      setUserId("");
      await queryClient.invalidateQueries({ queryKey: grantsKey });
    },
  });
  const removeGrant = useMutation({
    mutationFn: (grantId: string) => deleteDocumentGrant(token, document.documentId, grantId),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: grantsKey });
    },
  });
  const mode = access.data.accessMode;
  const isBusy = updateMode.isPending || addGrant.isPending || removeGrant.isPending;
  const mutationError = updateMode.error ?? addGrant.error ?? removeGrant.error;

  const submitGrant = (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (grantKind === "user" && userId.trim() === "") return;
    addGrant.mutate();
  };

  return (
    <div
      className="product-drawer-overlay"
      role="presentation"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget && !isBusy) onClose();
      }}
    >
      <section className="product-drawer access-drawer" role="dialog" aria-modal="true" aria-labelledby="access-drawer-title">
        <div className="product-drawer-heading">
          <div>
            <p className="eyebrow">{t("documents.access.eyebrow")}</p>
            <h2 id="access-drawer-title">{t("documents.access.title")}</h2>
            <p className="access-document-name">{document.filename}</p>
          </div>
          <button className="icon-button" type="button" aria-label={t("common.close")} title={t("common.close")} disabled={isBusy} onClick={onClose}>
            <X aria-hidden="true" />
          </button>
        </div>

        <section className="access-section" aria-labelledby="access-mode-title">
          <div className="access-section-heading">
            <div>
              <span className="access-section-icon"><ShieldCheck aria-hidden="true" /></span>
              <h3 id="access-mode-title">{t("documents.access.mode")}</h3>
            </div>
            {updateMode.isPending && <LoaderCircle className="spin" aria-label={t("documents.access.saving")} />}
          </div>
          <div className="access-mode-control" role="group" aria-label={t("documents.access.mode")}>
            <button
              type="button"
              className={mode === "tenant" ? "active" : undefined}
              aria-pressed={mode === "tenant"}
              disabled={isBusy}
              onClick={() => updateMode.mutate("tenant")}
            >
              <Building2 aria-hidden="true" />
              {t("documents.access.tenant")}
            </button>
            <button
              type="button"
              className={mode === "restricted" ? "active" : undefined}
              aria-pressed={mode === "restricted"}
              disabled={isBusy}
              onClick={() => updateMode.mutate("restricted")}
            >
              <LockKeyhole aria-hidden="true" />
              {t("documents.access.restricted")}
            </button>
          </div>
        </section>

        <section className="access-section" aria-labelledby="grants-title">
          <div className="access-section-heading">
            <div>
              <span className="access-section-icon"><Users aria-hidden="true" /></span>
              <h3 id="grants-title">{t("documents.access.grants")}</h3>
            </div>
            <span className="access-count">{grants.data?.length ?? 0}</span>
          </div>

          {grants.isPending && (
            <div className="access-state" role="status"><LoaderCircle className="spin" aria-hidden="true" />{t("documents.access.loading")}</div>
          )}
          {grants.isError && (
            <div className="access-state access-error" role="alert">{errorMessage(grants.error, t("documents.access.loadError"), t("common.requestId"))}</div>
          )}
          {grants.isSuccess && grants.data.length === 0 && (
            <div className="access-state">{t("documents.access.noGrants")}</div>
          )}
          {grants.isSuccess && grants.data.length > 0 && (
            <ul className="grant-list">
              {grants.data.map((grant) => {
                const isUser = grant.granteeUserId !== null;
                const label = isUser
                  ? grant.granteeUserId
                  : grant.granteeRole === "owner"
                    ? t("session.owner")
                    : t("session.member");
                return (
                  <li key={grant.grantId}>
                    <span className="grant-target-icon">{isUser ? <User aria-hidden="true" /> : <Users aria-hidden="true" />}</span>
                    <span><strong>{label}</strong><small>{isUser ? t("documents.access.user") : t("documents.access.role")}</small></span>
                    <button
                      className="icon-button danger-icon-button"
                      type="button"
                      aria-label={t("documents.access.remove")}
                      title={t("documents.access.remove")}
                      disabled={isBusy}
                      onClick={() => removeGrant.mutate(grant.grantId)}
                    >
                      <Trash2 aria-hidden="true" />
                    </button>
                  </li>
                );
              })}
            </ul>
          )}
        </section>

        <form className="access-section grant-form" onSubmit={submitGrant}>
          <div className="access-section-heading">
            <div>
              <span className="access-section-icon"><UserPlus aria-hidden="true" /></span>
              <h3>{t("documents.access.addGrant")}</h3>
            </div>
          </div>
          <div className="grant-kind-control" role="group" aria-label={t("documents.access.targetType")}>
            <button type="button" className={grantKind === "user" ? "active" : undefined} aria-pressed={grantKind === "user"} disabled={isBusy} onClick={() => setGrantKind("user")}>{t("documents.access.user")}</button>
            <button type="button" className={grantKind === "role" ? "active" : undefined} aria-pressed={grantKind === "role"} disabled={isBusy} onClick={() => setGrantKind("role")}>{t("documents.access.role")}</button>
          </div>
          {grantKind === "user" ? (
            <label className="access-field">
              <span>{t("documents.access.userId")}</span>
              <input type="text" value={userId} required pattern="[0-9a-fA-F-]{36}" placeholder="00000000-0000-0000-0000-000000000000" disabled={isBusy} onChange={(event) => setUserId(event.target.value)} />
            </label>
          ) : (
            <label className="access-field">
              <span>{t("documents.access.role")}</span>
              <select value={role} disabled={isBusy} onChange={(event) => setRole(event.target.value as "owner" | "member")}>
                <option value="member">{t("session.member")}</option>
                <option value="owner">{t("session.owner")}</option>
              </select>
            </label>
          )}
          <button className="command-button grant-submit" type="submit" disabled={isBusy || (grantKind === "user" && userId.trim() === "")}>
            {addGrant.isPending ? <LoaderCircle className="spin" aria-hidden="true" /> : <UserPlus aria-hidden="true" />}
            {t("documents.access.add")}
          </button>
        </form>

        {mutationError && <div className="access-mutation-error" role="alert">{errorMessage(mutationError, t("documents.access.saveError"), t("common.requestId"))}</div>}
      </section>
    </div>
  );
}
