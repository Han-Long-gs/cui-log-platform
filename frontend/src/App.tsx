import Header from "./components/Header.tsx";
import ToolBar from "./components/ToolBar.tsx";
import LogTable from "./components/LogTable.tsx";
import Pager from "./components/Pager.tsx";
import type { LogFilters, LogPage } from "./types.ts";
import { api } from "./api.ts";
import { useEffect, useState } from "react";

const PAGE_SIZE = 20;

function App() {
  const [appliedFilters, setAppliedFilters] = useState<LogFilters>({}); // only stores the selected filters in ToolBar
  const [data, setData] = useState<LogPage>({ next_cursor: null, items: [] });
  const [cursorStack, setCursorStack] = useState<string[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState(false);
  useEffect(() => {
    const abortController = new AbortController();
    api.logs
      .getPage(
        appliedFilters,
        abortController.signal,
        cursorStack.at(-1),
        PAGE_SIZE,
      )
      .then((page) => {
        setError(false);
        setData(page);
      })
      .catch((err) => {
        if (err.name === "AbortError") {
          return;
        }
        setError(true);
      })
      .finally(() => {
        if (abortController.signal.aborted) return;
        setLoading(false);
      });
    // clean up - avoid race condition
    return () => abortController.abort();
  }, [appliedFilters, cursorStack]);
  // TODO: mock data for now; removed after GET services/envs are implemented
  const mockServiceList = ["Query API", "Ingestion API", "Worker", "RAG"];
  const mockEnvList = [
    "test",
    "development",
    "staging",
    "production",
    "very very very very long string",
  ];
  const handleSearch = (draft: LogFilters) => {
    setLoading(true);
    setCursorStack([]);
    setAppliedFilters(draft);
  };
  const handlePrev = () => {
    setLoading(true);
    setCursorStack((prev) => prev.slice(0, -1));
  };
  const handleNext = () => {
    const nextCursor = data?.next_cursor;
    if (!nextCursor) return;
    setLoading(true);
    setCursorStack((prev) => [...prev, nextCursor]);
  };

  return (
    <main className="page">
      <Header logoText="cui" />
      <ToolBar
        serviceList={mockServiceList}
        envList={mockEnvList}
        onSearch={handleSearch}
      />
      {error && <span className="text-body">Error occurs...</span>}
      <LogTable logs={data.items} isLoading={loading} />
      <Pager
        pageNum={cursorStack.length + 1}
        canPrev={!loading && cursorStack.length > 0}
        canNext={!loading && data.next_cursor != null}
        onPrev={handlePrev}
        onNext={handleNext}
      />
    </main>
  );
}

export default App;
