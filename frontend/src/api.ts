import type {
  LogPage,
  LogFilters,
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

function buildSearchParamsStr(filters: LogFilters | StatsFilters): string {
  const params = new URLSearchParams();
  for (const [key, value] of Object.entries(filters)) {
    if (value != null && value !== "") {
      params.set(key, String(value));
    }
  }
  return params.toString();
}

export const api = {
  logs: {
    getPage: (filters: LogFilters, signal: AbortSignal): Promise<LogPage> => {
      return request<LogPage>(`/api/v1/logs?${buildSearchParamsStr(filters)}`, {
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
