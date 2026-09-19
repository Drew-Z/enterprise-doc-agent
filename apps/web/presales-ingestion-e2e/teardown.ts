export default async function teardown() {
  const response = await fetch("http://127.0.0.1:18766/__ingestion_test__/shutdown", {
    method: "POST", headers: { "X-Presales-Test": "presales-ingestion" },
    signal: AbortSignal.timeout(50_000),
  });
  if (!response.ok) throw new Error("Ingestion acceptance cleanup failed.");
  const body = await response.json() as { success: boolean; workerStopped: boolean; remainingTestTenants: number; remainingTestUsers: number; remainingObjects: number; remainingMultipartUploads: number; remainingRedisKeys: number };
  if (!body.success || !body.workerStopped || [body.remainingTestTenants, body.remainingTestUsers, body.remainingObjects, body.remainingMultipartUploads, body.remainingRedisKeys].some(count => count !== 0)) {
    throw new Error("Ingestion acceptance resources remain; inspect cleanup.json.");
  }
  console.log("Ingestion cleanup: worker stopped; 0 test tenants, users, objects, multipart uploads and Redis keys remain.");
}
