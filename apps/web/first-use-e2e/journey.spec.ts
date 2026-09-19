import { expect, test } from "@playwright/test";
import { writeFile } from "node:fs/promises";
import { acceptInvitation, admit, business, control, fixture, invite, origin, prepare, privacyAndLayout, scrub, selectEnterprise, session, signIn, switchEnterprise } from "./support";
import { assertIdentityProvider, assertReady, identityEvidenceSchema, snapshot } from "./pipeline";
import { identityProvider } from "./support";
import { rejectOldPasswordAndSignIn, resetKeycloakPassword } from "./keycloak";
import { createSheet, documentRow, generate, reviewAndExport, sourceAttempt, upload, type Source } from "./workspace";
import { observeNetwork } from "./network";
import { usagePage } from "./usage";
import { packetSchema } from "../src/presales/api";
import { switchWithDelayedUsage } from "./boundaries";

const enterpriseA = "澄明试用企业";
const enterpriseB = "远山试用企业";

test.beforeEach(async ({ context }) => { await prepare(context); });
test.afterEach(async ({ context }) => { await scrub(context); });

test("first use: browser admission, invitations, ingestion, review, usage and isolation", async ({ browser, context, page, request }, info) => {
  const inputs = await fixture(request);
  const colleagueContext = await browser.newContext({ baseURL: origin, locale: "zh-CN", viewport: { width: 390, height: 844 } });
  await prepare(colleagueContext);
  const colleague = await colleagueContext.newPage();
  const collectNetwork = observeNetwork([page, colleague]);
  try {
    const { tenantA, tenantB } = await test.step("S1: owner opens two enterprises through browser admission", async () => {
      await signIn(page, "企业管理员");
      await expect(page.locator(".browser-empty")).toContainText("此账号暂无可访问的企业");
      const tenantA = await admit(page, inputs.admissionCodes.a, enterpriseA);
      const tenantB = await admit(page, inputs.admissionCodes.b, enterpriseB);
      expect(tenantA).not.toBe(tenantB);
      await control(request, "/test/configure", { tenant_id: tenantA, request_limit: 4 });
      await control(request, "/test/configure", { tenant_id: tenantB, request_limit: 1 });
      await selectEnterprise(page, enterpriseA);
      await page.reload();
      await expect(page.locator(".session-identity")).toContainText(enterpriseA);
      const identity = await session(page);
      expect(identity.currentTenant).toMatchObject({ tenantId: tenantA, role: "owner" });
      const cookie = (await context.cookies()).find(item => item.name === "__Host-docagent-session");
      const cookieFlags = cookie ? { secure: cookie.secure, httpOnly: cookie.httpOnly, sameSite: cookie.sameSite, domain: cookie.domain, path: cookie.path, opaque: /^bss1_[A-Za-z0-9_-]{43}$/.test(cookie.value) } : null;
      expect(cookieFlags).toEqual({ secure: true, httpOnly: true, sameSite: "Lax", domain: "127.0.0.1", path: "/", opaque: true });
      const state = identityEvidenceSchema.parse(await (await control(request, "/test/state")).json());
      expect(state.userCount).toBe(1);
      expect(state.activeMembers).toEqual({ [enterpriseA]: 1, [enterpriseB]: 1 });
      assertIdentityProvider(state, 1);
      await privacyAndLayout(page);
      await page.screenshot({ path: info.outputPath("desktop-admitted.png"), fullPage: true });
      await writeFile(info.outputPath("admission-evidence.json"), JSON.stringify({ tenantA, tenantB, state, cookieFlags }, null, 2) + "\n");
      return { tenantA, tenantB };
    });

    await test.step("S1: one colleague accepts both invitations and becomes a member on mobile", async () => {
      const tokenA = await invite(page, "desktop-colleague@example.test");
      await switchEnterprise(page, enterpriseB);
      const tokenB = await invite(page, "desktop-colleague@example.test");
      const pending = identityEvidenceSchema.parse(await (await control(request, "/test/state")).json());
      expect(pending.activeMembers).toEqual({ [enterpriseA]: 1, [enterpriseB]: 1 });
      await signIn(colleague, "桌面受邀同事");
      await acceptInvitation(colleague, tokenA, enterpriseA);
      const first = await session(colleague);
      expect(first.currentTenant).toMatchObject({ tenantId: tenantA, role: "member" });
      await acceptInvitation(colleague, tokenB, enterpriseB);
      const second = await session(colleague);
      expect(second.currentTenant).toMatchObject({ tenantId: tenantB, role: "member" });
      const joined = identityEvidenceSchema.parse(await (await control(request, "/test/state")).json());
      expect(joined.userCount).toBe(2);
      expect(joined.activeMembers).toEqual({ [enterpriseA]: 2, [enterpriseB]: 2 });
      assertIdentityProvider(joined, 2);
      await privacyAndLayout(colleague);
      await colleague.screenshot({ path: info.outputPath("mobile-joined.png"), fullPage: true });
      await writeFile(info.outputPath("invitation-evidence.json"), JSON.stringify({ pending, joined }, null, 2) + "\n");
      await switchEnterprise(page, enterpriseA);
      await switchEnterprise(colleague, enterpriseA);
    });

    const { sources, ownerReview, memberReview } = await test.step("S2: real TXT PDF DOCX uploads reach ready through the worker and both roles review and export", async () => {
      await page.goto("/#/documents");
      await control(request, "/test/publisher/pause", {});
      const sources: Source[] = [];
      for (const key of ["txt", "pdf", "docx"] as const) {
        const file = inputs.fixtures[key];
        sources.push({ file, versionId: await upload(page, request, key, file) });
      }
      for (const source of sources) {
        const row = documentRow(page, source.file.name);
        await expect(row.locator(".status-badge")).toHaveText("处理中");
        await expect(row.getByRole("button", { name: "创建响应表" })).toBeDisabled();
      }
      const pending = await snapshot(request);
      expect(pending.versions).toHaveLength(3);
      expect(pending.generations).toHaveLength(0);
      expect(pending.jobs.every(job => job.status === "pending" && job.attempts === 0)).toBe(true);
      expect(pending.outbox.every(event => event.status === "pending" && event.attempts === 0)).toBe(true);
      const unavailable = await business(page, "/api/presales", { method: "POST", data: sourceAttempt(sources[0].versionId) });
      expect(unavailable.status).toBe(404);
      expect(unavailable.body).toMatchObject({ error: { code: "presales_source_unavailable" } });
      await page.screenshot({ path: info.outputPath("desktop-processing.png"), fullPage: true });
      await control(request, "/test/publisher/resume", {});
      for (const source of sources) await expect(documentRow(page, source.file.name).getByRole("button", { name: "创建响应表" })).toBeEnabled();
      const ready = await snapshot(request);
      for (const source of sources) assertReady(ready, source.file, source.versionId, tenantA);
      await page.screenshot({ path: info.outputPath("desktop-ready.png"), fullPage: true });
      const ownerPacket = await createSheet(page, sources, "管理员 · 三格式资料响应", "Retention 数据保留期限\t要求 1\nExport 导出格式\t要求 2\nBackups 备份保留期限\t要求 3");
      await generate(page, 3);
      const ownerReview = await reviewAndExport(page, sources, ownerPacket, info, "desktop-owner");
      const privateOwner = await business(colleague, "/api/presales/" + ownerPacket.id);
      expect(privateOwner.status).toBe(404);
      expect(privateOwner.body).toMatchObject({ error: { code: "presales_not_found" } });
      const memberPacket = await createSheet(colleague, [sources[0]], "同事 · 保留期限响应", "Retention 同事复核保留期限\t同事要求 1");
      await generate(colleague, 1);
      const memberReview = await reviewAndExport(colleague, [sources[0]], memberPacket, info, "mobile-member");
      const privateMember = await business(page, "/api/presales/" + memberPacket.id);
      expect(privateMember.status).toBe(404);
      expect(privateMember.body).toMatchObject({ error: { code: "presales_not_found" } });
      for (const review of [ownerReview, memberReview]) for (const row of review.packet.rows) for (const citation of row.draft?.citations ?? []) {
        expect(ready.chunks.find(chunk => chunk.id === citation.chunkId)?.text).toContain(citation.excerpt);
      }
      await writeFile(info.outputPath("pipeline-evidence.json"), JSON.stringify({ pending, ready, ownerReview, memberReview, rejections: { unavailable, privateOwner, privateMember }, final: await snapshot(request), network: await collectNetwork(3) }, null, 2) + "\n");
      return { sources, ownerReview, memberReview };
    });

    await test.step("S3: exhausted capacity rejects before model dispatch and the owner sees exact resources", async () => {
      const exhausted = await createSheet(page, [sources[0]], "额度耗尽后的要求", "Retention 零额度拒绝");
      const before = await snapshot(request);
      expect(before.entitlements.find(item => item.tenantId === tenantA)).toMatchObject({ limit: 4, used: 4, reserved: 0 });
      const rejected = page.waitForResponse(response => new URL(response.url()).pathname.endsWith("/generate") && response.request().method() === "POST");
      await page.getByRole("button", { name: "生成待处理要求", exact: true }).click();
      const response = await rejected;
      expect(response.status()).toBe(429);
      await expect(page.locator(".presales-error")).toContainText("可用生成额度不足");
      const after = await snapshot(request);
      expect(after.modelRequests).toEqual(before.modelRequests);
      expect(after.presalesAttempts).toEqual(before.presalesAttempts);
      expect(after.reservations).toEqual(before.reservations);
      expect(after.usageEvents).toEqual(before.usageEvents);
      const unchanged = packetSchema.parse((await business(page, "/api/presales/" + exhausted.id)).body);
      expect(unchanged.rows[0]).toMatchObject({ state: "pending", attempts: [], draft: null, review: null });
      const usage = await usagePage(page, tenantA, after);
      expect(usage.resources.storageUsedBytes).toBe(sources.reduce((sum, source) => sum + source.file.sizeBytes, 0));
      expect(usage.recentEvents).toHaveLength(4);
      await privacyAndLayout(page);
      await page.screenshot({ path: info.outputPath("desktop-exhausted-usage.png"), fullPage: true });
      await colleague.goto("/#/usage");
      await expect(colleague.getByRole("alert")).toContainText("仅企业管理员可以查看用量");
      const memberUsage = await business(colleague, "/api/tenant-usage");
      expect(memberUsage.status).toBe(403);
      const rejectionBody: unknown = await response.json();
      await writeFile(info.outputPath("quota-evidence.json"), JSON.stringify({ before, after, rejected: { status: response.status(), body: rejectionBody }, unchanged, usage, memberUsage }, null, 2) + "\n");
    });

    const { recoveryReview } = await test.step("S3: mobile parser recovery and one HTTP model failure release capacity before an explicit retry consumes it", async () => {
      await switchEnterprise(page, enterpriseB);
      await switchEnterprise(colleague, enterpriseB);
      const foreignSource = await business(page, "/api/presales", { method: "POST", data: sourceAttempt(sources[0].versionId) });
      const foreignPacket = await business(page, "/api/presales/" + ownerReview.packet.id);
      expect(foreignSource.status).toBe(404);
      expect(foreignSource.body).toMatchObject({ error: { code: "presales_source_unavailable" } });
      expect(foreignPacket.status).toBe(404);
      expect(foreignPacket.body).toMatchObject({ error: { code: "presales_not_found" } });
      await colleague.goto("/#/documents");
      const brokenId = await upload(colleague, request, "broken_pdf", inputs.fixtures.broken_pdf);
      const broken = documentRow(colleague, inputs.fixtures.broken_pdf.name);
      await expect(broken.locator(".status-badge")).toHaveText("失败");
      await expect(broken).toContainText("pdf_parse_failed");
      await expect(broken.getByRole("button", { name: "创建响应表" })).toBeDisabled();
      const brokenSource = await business(colleague, "/api/presales", { method: "POST", data: sourceAttempt(brokenId) });
      expect(brokenSource.status).toBe(404);
      await privacyAndLayout(colleague);
      await colleague.screenshot({ path: info.outputPath("mobile-parse-failure.png"), fullPage: true });
      const repaired = { file: inputs.fixtures.repaired_pdf, versionId: await upload(colleague, request, "repaired_pdf", inputs.fixtures.repaired_pdf) };
      await expect(documentRow(colleague, repaired.file.name).getByRole("button", { name: "创建响应表" })).toBeEnabled();
      const ready = await snapshot(request);
      assertReady(ready, repaired.file, repaired.versionId, tenantB);
      expect(ready.versions.find(item => item.id === brokenId)).toMatchObject({ status: "failed", sha256: inputs.fixtures.broken_pdf.sha256, objectSha256: inputs.fixtures.broken_pdf.sha256 });
      expect(ready.generations.find(item => item.versionId === brokenId)).toMatchObject({ status: "failed", active: false, errorCode: "pdf_parse_failed" });
      expect(ready.jobs.find(item => item.versionId === brokenId)).toMatchObject({ status: "dead", attempts: 1, errorCode: "pdf_parse_failed" });
      expect(ready.chunks.filter(item => item.versionId === brokenId)).toHaveLength(0);
      const recoveryPacket = await createSheet(colleague, [repaired], "同事 · 修复文件与模型重试", "Export 修复资料导出格式");
      await control(request, "/test/model/fail-next", {});
      const failed = colleague.waitForResponse(response => new URL(response.url()).pathname.endsWith("/generate") && response.request().method() === "POST");
      await colleague.getByRole("button", { name: "生成待处理要求", exact: true }).click();
      const failedPacket = packetSchema.parse(await (await failed).json());
      expect(failedPacket.rows[0]).toMatchObject({ state: "failed", draft: null, attempts: [{ state: "failed", errorCode: "presales_model_failed", providerRequestCount: 1 }] });
      await expect(colleague.getByRole("article", { name: "R1", exact: true }).getByRole("alert")).toContainText("失败记录已保存");
      await expect(colleague.getByRole("button", { name: "重试本条", exact: true })).toBeEnabled();
      const released = await snapshot(request);
      expect(released.entitlements.find(item => item.tenantId === tenantB)).toMatchObject({ limit: 1, used: 0, reserved: 0 });
      expect(released.reservations.filter(item => item.tenantId === tenantB)).toEqual([expect.objectContaining({ state: "released" })]);
      expect(released.usageEvents.filter(item => item.tenantId === tenantB)).toEqual([expect.objectContaining({ eventType: "release", quantity: 1, estimatedCost: null })]);
      const releasedUsage = await usagePage(page, tenantB, released);
      await colleague.reload();
      await expect(colleague.getByRole("button", { name: "重试本条", exact: true })).toBeEnabled();
      expect((await snapshot(request)).modelRequests).toEqual(released.modelRequests);
      await colleague.screenshot({ path: info.outputPath("mobile-model-failure.png"), fullPage: true });
      await colleague.getByRole("button", { name: "重试本条", exact: true }).click();
      await expect(colleague.locator(".presales-answer")).toHaveCount(1);
      const recoveryReview = await reviewAndExport(colleague, [repaired], recoveryPacket, info, "mobile-recovered");
      expect(recoveryReview.packet.rows[0].attempts.map(item => item.state)).toEqual(["failed", "succeeded"]);
      const consumed = await snapshot(request);
      expect(consumed.entitlements.find(item => item.tenantId === tenantB)).toMatchObject({ limit: 1, used: 1, reserved: 0 });
      expect(consumed.usageEvents.filter(item => item.tenantId === tenantB).map(item => item.eventType).sort()).toEqual(["consume", "release"]);
      expect(consumed.modelRequests).toHaveLength(6);
      expect(consumed.modelRequests.filter(item => item.controlledFailure)).toEqual([{ status: 503, controlledFailure: true }]);
      expect(consumed.modelSuccesses).toHaveLength(5);
      const consumedUsage = await usagePage(page, tenantB, consumed);
      expect(consumedUsage.resources.storageUsedBytes).toBe(inputs.fixtures.broken_pdf.sizeBytes + repaired.file.sizeBytes);
      await page.screenshot({ path: info.outputPath("desktop-recovered-usage.png"), fullPage: true });
      await writeFile(info.outputPath("recovery-evidence.json"), JSON.stringify({ brokenId, repaired, ready, failedPacket, released, releasedUsage, recoveryReview, consumed, consumedUsage, priorPackets: [ownerReview.packet.id, memberReview.packet.id], rejections: { foreignSource, foreignPacket, brokenSource }, network: await collectNetwork(5) }, null, 2) + "\n");
      return { recoveryReview };
    });

    const secondTab = await test.step("S4: an actual delayed usage response cannot cross an enterprise switch in either tab", async () => {
      return switchWithDelayedUsage(page, context, await snapshot(request), { name: enterpriseA, id: tenantA }, { name: enterpriseB, id: tenantB }, info);
    });

    await test.step("S4: deactivation blocks old evidence access while the colleague's other enterprise remains available", async () => {
      const credential = await session(colleague);
      const actorId = credential.currentTenant?.actorId;
      expect(credential.currentTenant?.tenantId).toBe(tenantB);
      await page.goto("/#/identity");
      const member = page.locator(".member-directory-list article").filter({ hasText: "desktop-colleague@example.test" });
      await member.getByRole("button", { name: "停用成员", exact: true }).click();
      await expect(member.getByRole("button", { name: "恢复成员", exact: true })).toBeEnabled();
      const denied = await business(colleague, "/api/presales/" + recoveryReview.packet.id, { credential });
      expect(denied.status).toBe(403);
      expect(denied.body).toMatchObject({ error: { code: "browser_principal_forbidden" } });
      await colleague.locator(".presales-packet-heading").getByRole("button", { name: "刷新", exact: true }).click();
      await expect(colleague.getByRole("heading", { name: "选择企业", exact: true })).toBeVisible();
      await expect(colleague.locator(".presales-answer")).toHaveCount(0);
      await expect(colleague.getByRole("button", { name: "进入 " + enterpriseB, exact: true })).toHaveCount(0);
      await colleague.screenshot({ path: info.outputPath("mobile-revoked.png"), fullPage: true });
      await selectEnterprise(colleague, enterpriseA);
      await colleague.goto("/#/presales");
      await expect(colleague.locator(".presales-packet-heading")).toContainText(memberReview.packet.title);
      await expect(colleague.locator(".presales-reviewed")).toHaveCount(1);
      const state = await snapshot(request);
      expect(state.memberships.find(item => item.tenantId === tenantB && item.actorId === actorId)?.isActive).toBe(false);
      expect(state.memberships.find(item => item.tenantId === tenantA && item.actorId === actorId)?.isActive).toBe(true);
      const usage = await usagePage(page, tenantB, state);
      expect(usage.resources.seatsUsed).toBe(1);
      expect(usage.resources.seatsRemaining).toBe(1);
      await page.setViewportSize({ width: 390, height: 844 });
      await privacyAndLayout(page);
      await page.screenshot({ path: info.outputPath("mobile-owner-usage.png"), fullPage: true });
      await page.setViewportSize({ width: 1440, height: 1000 });
      await writeFile(info.outputPath("revocation-evidence.json"), JSON.stringify({ denied, state, usage, retainedEnterprise: (await session(colleague)).currentTenant?.tenantId }, null, 2) + "\n");
    });

    await test.step("S4: both roles sign out and former sessions cannot read business data", async () => {
      const ownerCredential = await session(page);
      const memberCredential = await session(colleague);
      await page.getByRole("button", { name: "退出会话", exact: true }).click();
      await expect(page.getByRole("link", { name: "使用企业账号登录" })).toBeVisible();
      await expect(secondTab.getByRole("link", { name: "使用企业账号登录" })).toBeVisible();
      const ownerDenied = await business(page, "/api/tenant-usage", { credential: ownerCredential });
      expect(ownerDenied.status).toBe(401);
      await colleague.getByRole("button", { name: "退出会话", exact: true }).click();
      await expect(colleague.getByRole("link", { name: "使用企业账号登录" })).toBeVisible();
      const memberDenied = await business(colleague, "/api/presales/" + memberReview.packet.id, { credential: memberCredential });
      expect(memberDenied.status).toBe(401);
      for (const target of [page, colleague, secondTab]) {
        await privacyAndLayout(target);
        expect(await target.evaluate(() => [localStorage, sessionStorage].some(storage => Object.values(storage).some(value => typeof value === "string" && /Retention is 30 days|受控验收输出|已核对上传资料/.test(value))))).toBe(false);
      }
      expect((await context.cookies()).some(item => item.name === "__Host-docagent-session")).toBe(false);
      expect((await colleagueContext.cookies()).some(item => item.name === "__Host-docagent-session")).toBe(false);
      await page.screenshot({ path: info.outputPath("desktop-signed-out.png"), fullPage: true });
      await writeFile(info.outputPath("final-client-evidence.json"), JSON.stringify({ ownerDenied, memberDenied, network: await collectNetwork(5), state: await snapshot(request) }, null, 2) + "\n");
    });
    if (identityProvider() === "keycloak") {
      await test.step("S5: real password reset rejects the old password and retains product identity", async () => {
        const before = await snapshot(request);
        const actorId = before.memberships.find(item => item.tenantId === tenantA && item.role === "owner")?.actorId;
        expect(actorId).toBeTruthy();
        const reset = await resetKeycloakPassword(page, { control: route => control(request, route), webOrigin: origin });
        await selectEnterprise(page, enterpriseA);
        const resetSession = await session(page);
        expect(resetSession.currentTenant).toMatchObject({ tenantId: tenantA, actorId, role: "owner" });
        expect((await business(page, "/api/tenant-usage")).status).toBe(200);
        await page.getByRole("button", { name: "退出会话", exact: true }).click();
        await expect(page.getByRole("link", { name: "使用企业账号登录" })).toBeVisible();
        const fresh = await browser.newContext({ baseURL: origin, locale: "zh-CN" });
        await prepare(fresh);
        try {
          const renewed = await fresh.newPage();
          const login = await rejectOldPasswordAndSignIn(renewed, { control: route => control(request, route), webOrigin: origin });
          await selectEnterprise(renewed, enterpriseB);
          const renewedSession = await session(renewed);
          expect(renewedSession.currentTenant).toMatchObject({ tenantId: tenantB, actorId, role: "owner" });
          const after = await snapshot(request);
          expect(after.userCount).toBe(2);
          expect(after.keycloak).toMatchObject({ verifiedUsers: 2, successfulCodeExchanges: 4, passwordUpdates: 1, capturedEmails: 3, bindingCount: 4, bindingsMatchKeycloak: true });
          await privacyAndLayout(renewed);
          await renewed.screenshot({ path: info.outputPath("keycloak-reset-product-identity.png"), fullPage: true });
          await renewed.getByRole("button", { name: "退出会话", exact: true }).click();
          await expect(renewed.getByRole("link", { name: "使用企业账号登录" })).toBeVisible();
          expect((await business(renewed, "/api/tenant-usage", { credential: renewedSession })).status).toBe(401);
          await writeFile(info.outputPath("keycloak-reset-evidence.json"), JSON.stringify({ reset, login, originalActorPreserved: true, userCount: after.userCount, keycloak: after.keycloak, signedOutAccessDenied: true }, null, 2) + "\n");
        } finally {
          await scrub(fresh);
          await fresh.close();
        }
      });
    }
  } finally {
    await scrub(colleagueContext);
    if (browser.isConnected()) await colleagueContext.close();
  }
});
