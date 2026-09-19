export interface AuthEntry {
  readonly admissionToken: string | null;
  readonly invitationToken?: string | null;
  signInFailed: boolean;
  admissionLink: boolean;
  invitationLink?: boolean;
  releaseSecrets?(): void;
}

/** Call once before rendering. A link secret is never kept in browser history or storage. */
export function consumeAuthEntry(): AuthEntry {
  const hash = window.location.hash;
  const admissionLink = hash.startsWith("#/admission?");
  const invitationLink = hash === "#/invitation" || hash.startsWith("#/invitation?");
  const signInFailed = hash === "#/signin?error=sign_in_failed";
  const params = new URLSearchParams(hash.includes("?") ? hash.slice(hash.indexOf("?") + 1) : "");
  const tokens = params.getAll("token");
  const candidate = tokens[0] ?? "";
  const singleToken = tokens.length === 1 && candidate.length === 48 && [...params.keys()].length === 1;
  let admissionToken = admissionLink && singleToken && /^adm1_[A-Za-z0-9_-]{43}$/.test(candidate) ? candidate : null;
  let invitationToken = invitationLink && singleToken && /^inv1_[A-Za-z0-9_-]{43}$/.test(candidate) ? candidate : null;
  if (admissionLink || invitationLink || signInFailed) {
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
  }
  return {
    get admissionToken() { return admissionToken; },
    get invitationToken() { return invitationToken; },
    signInFailed, admissionLink, invitationLink,
    releaseSecrets() { admissionToken = null; invitationToken = null; },
  };
}
