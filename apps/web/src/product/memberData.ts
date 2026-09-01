import type { TenantMember } from "./membersApi";

export const showcaseMembers: TenantMember[] = [
  {
    membershipId: "00000000-0000-4000-8000-000000000051",
    tenantId: "00000000-0000-4000-8000-000000000001",
    userId: "00000000-0000-4000-8000-000000000002",
    email: "owner@example.com",
    role: "owner",
    isActive: true,
    createdAt: "2026-06-01T08:00:00+00:00",
    updatedAt: "2026-08-26T08:00:00+00:00",
  },
  {
    membershipId: "00000000-0000-4000-8000-000000000052",
    tenantId: "00000000-0000-4000-8000-000000000001",
    userId: "00000000-0000-4000-8000-000000000005",
    email: "reviewer@example.com",
    role: "member",
    isActive: true,
    createdAt: "2026-06-18T03:30:00+00:00",
    updatedAt: "2026-08-22T06:30:00+00:00",
  },
  {
    membershipId: "00000000-0000-4000-8000-000000000053",
    tenantId: "00000000-0000-4000-8000-000000000001",
    userId: "00000000-0000-4000-8000-000000000006",
    email: "former.member@example.com",
    role: "member",
    isActive: false,
    createdAt: "2026-07-10T03:00:00+00:00",
    updatedAt: "2026-08-20T06:30:00+00:00",
  },
];
