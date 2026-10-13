/**
 * Circuit Breaker: Trips globally after 3 push-gate violations.
 */

export interface ViolationRecord {
  timestamp: string;
  reason: string;
}

export class CircuitBreaker {
  public static readonly DEFAULT_THRESHOLD = 3;
  private violations: ViolationRecord[] = [];
  private tripped: boolean = false;
  private trippedAt: string | null = null;

  constructor(private readonly threshold: number = CircuitBreaker.DEFAULT_THRESHOLD) {}

  public recordViolation(reason: string): boolean {
    this.violations.push({
      timestamp: new Date().toISOString(),
      reason,
    });

    if (this.violations.length >= this.threshold && !this.tripped) {
      this.tripped = true;
      this.trippedAt = new Date().toISOString();
      return true;
    }
    return false;
  }

  public isTripped(): boolean {
    return this.tripped;
  }

  public getViolationCount(): number {
    return this.violations.length;
  }

  public getViolations(): ReadonlyArray<ViolationRecord> {
    return [...this.violations];
  }

  public reset(operatorId: string): void {
    this.violations = [];
    this.tripped = false;
    this.trippedAt = null;
  }
}

export const globalCircuitBreaker = new CircuitBreaker();
