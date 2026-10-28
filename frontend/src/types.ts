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

// request shape for query-api GET /api/v1/logs
export type LogFilters = {
  service_name?: string;
  environment?: string;
  level?: Level;
  start?: string; // ISO 8601 string with timezone
  end?: string; // ISO 8601 string with timezone
  cursor?: string;
  limit?: number;
};

// response shape from query-api GET /api/v1/logs
export type LogPage = {
  next_cursor: string | null;
  items: LogItem[];
};

// request shape for query-api GET /api/v1/stats
export type StatsFilters = {
  service_name?: string;
  environment?: string;
  start?: string; // ISO 8601 string with timezone
  end?: string; // ISO 8601 string with timezone
};

// response shape for query-api GET /api/v1/stats
export type StatsResponse = {
  counts: Record<Level, number>;
};
