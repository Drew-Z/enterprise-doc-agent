import type { IdentityBinding } from "./identityBindingsApi";

export const showcaseIdentityBindings: IdentityBinding[] = [
  {
    bindingId: "00000000-0000-4000-8000-000000000041",
    tenantId: "00000000-0000-4000-8000-000000000001",
    issuer: "https://login.example.com/enterprise",
    subject: "00u7f4b2a9d3example",
    userId: "00000000-0000-4000-8000-000000000002",
    userEmail: "owner@example.com",
    isActive: true,
    createdAt: "2026-08-26T08:00:00+00:00",
    updatedAt: "2026-08-26T08:00:00+00:00",
  },
  {
    bindingId: "00000000-0000-4000-8000-000000000042",
    tenantId: "00000000-0000-4000-8000-000000000001",
    issuer: "https://login.example.com/enterprise",
    subject: "00u8c1d5e7f9former",
    userId: "00000000-0000-4000-8000-000000000006",
    userEmail: "former.member@example.com",
    isActive: false,
    createdAt: "2026-07-10T03:00:00+00:00",
    updatedAt: "2026-08-20T06:30:00+00:00",
  },
];
