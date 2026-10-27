export type Level = "INFO" | "WARNING" | "DEBUG" | "ERROR" | "CRITICAL";
export const LEVELS = [
  "INFO",
  "WARNING",
  "DEBUG",
  "ERROR",
  "CRITICAL",
] as const;

export type LogItem = {
  id: number;
  service_name: string;
  environment: string;
  level: Level;
  message: string;
  occurred_at: string;
  trace_id: string | null;
  custom: Record<string, unknown> | null;
};
