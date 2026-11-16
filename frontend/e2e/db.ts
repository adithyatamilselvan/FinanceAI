import { execFileSync } from "node:child_process";

// Used as both globalSetup and globalTeardown: drops ONLY the dedicated e2e database.
const PY = process.platform === "win32" ? "..\\backend\\.venv\\Scripts\\python.exe" : "../backend/.venv/bin/python";

export default async function dropE2eDatabase() {
  const uri = process.env.E2E_MONGODB_URI ?? "mongodb://localhost:27017";
  execFileSync(PY, ["-c", "import sys;from pymongo import MongoClient;c=MongoClient(sys.argv[1],serverSelectionTimeoutMS=4000);c.drop_database('financeai_e2e_run');c.close()", uri], { stdio: "inherit" });
}
