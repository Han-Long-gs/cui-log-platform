import type {
  LogPage,
  LogFilters,
  LogQuery,
  StatsFilters,
  StatsResponse,
} from "./types.ts";

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(path, options);
  if (!res.ok) {
    throw new ApiError(res.status, await res.text());
  }
  return (await res.json()) as T;
}

function buildSearchParamsStr(
  query: Record<string, string | number | undefined>,
): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(query)) {
    if (value != null && value !== "") {
      params.set(key, String(value));
    }
  }
  return params.toString();
}

export const api = {
  logs: {
    getPage: (
      filters: LogFilters,
      signal: AbortSignal,
      cursor?: string,
      limit?: number,
    ): Promise<LogPage> => {
      const query: LogQuery = { ...filters, cursor: cursor, limit: limit };
      return request<LogPage>(`/api/v1/logs?${buildSearchParamsStr(query)}`, {
        method: "GET",
        signal: signal,
      });
    },
  },
  stats: {
    getStats: (
      filters: StatsFilters,
      signal: AbortSignal,
    ): Promise<StatsResponse> => {
      return request<StatsResponse>(
        `/api/v1/stats?${buildSearchParamsStr(filters)}`,
        { method: "GET", signal: signal },
      );
    },
  },
};
