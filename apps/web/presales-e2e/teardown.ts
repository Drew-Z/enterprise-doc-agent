export default async function teardown() {
  const response = await fetch("http://127.0.0.1:18765/__presales_test__/shutdown", {
    method: "POST", headers: { "X-Presales-Test": "presales-browser" },
    signal: AbortSignal.timeout(15_000),
  });
  if (!response.ok) throw new Error("Presales browser fixture cleanup failed.");
  const body = await response.json() as { remainingTestTenants: number };
  if (body.remainingTestTenants !== 0) throw new Error("Presales test tenants remain.");
  console.log("Presales browser cleanup: 0 test tenants remain.");
}
