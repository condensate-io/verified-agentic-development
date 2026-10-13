/**
 * Push Gate: git ls-remote plus a reflog check before a run enters the staged state.
 */

import { execSync } from "child_process";
import { CircuitBreaker, globalCircuitBreaker } from "./circuitBreaker";

export interface PushGateResult {
  allowed: boolean;
  reason: string;
  commitSha: string;
  branch: string;
  remoteChecked: boolean;
  reflogVerified: boolean;
}

export class PushGate {
  constructor(
    private readonly repoDir: string = ".",
    private readonly remote: string = "origin",
    private readonly circuitBreaker: CircuitBreaker = globalCircuitBreaker
  ) {}

  public checkStagingReadiness(branch: string, commitSha: string): PushGateResult {
    // 0. Circuit Breaker Check
    if (this.circuitBreaker.isTripped()) {
      return {
        allowed: false,
        reason: "Global circuit breaker is TRIPPED due to 3 push-gate violations. Staging is locked.",
        commitSha,
        branch,
        remoteChecked: false,
        reflogVerified: false,
      };
    }

    // 1. git ls-remote check
    try {
      execSync(`git ls-remote --heads ${this.remote} ${branch}`, {
        cwd: this.repoDir,
        stdio: "pipe",
        encoding: "utf-8",
      });
    } catch (err: any) {
      const errMsg = err?.message || String(err);
      this.circuitBreaker.recordViolation(`git ls-remote failed: ${errMsg}`);
      return {
        allowed: false,
        reason: `PushGate ls-remote failed: ${errMsg}`,
        commitSha,
        branch,
        remoteChecked: false,
        reflogVerified: false,
      };
    }

    // 2. git reflog check: ensure commit exists locally in reflog
    try {
      const reflogOutput = execSync("git reflog show --format=%H -n 100", {
        cwd: this.repoDir,
        stdio: "pipe",
        encoding: "utf-8",
      });
      const lines = reflogOutput
        .split("\n")
        .map((l) => l.trim().toLowerCase())
        .filter(Boolean);

      const normSha = commitSha.toLowerCase();
      const found = lines.some((l) => l.startsWith(normSha) || normSha.startsWith(l));

      if (!found) {
        this.circuitBreaker.recordViolation(
          `Commit ${commitSha} was not found in local git reflog history`
        );
        return {
          allowed: false,
          reason: `PushGate violation: commit ${commitSha} does not exist in local git reflog`,
          commitSha,
          branch,
          remoteChecked: true,
          reflogVerified: false,
        };
      }
    } catch (err: any) {
      const errMsg = err?.message || String(err);
      this.circuitBreaker.recordViolation(`git reflog check failed: ${errMsg}`);
      return {
        allowed: false,
        reason: `PushGate reflog query failed: ${errMsg}`,
        commitSha,
        branch,
        remoteChecked: true,
        reflogVerified: false,
      };
    }

    return {
      allowed: true,
      reason: "PushGate passed: git ls-remote and reflog verified.",
      commitSha,
      branch,
      remoteChecked: true,
      reflogVerified: true,
    };
  }
}
