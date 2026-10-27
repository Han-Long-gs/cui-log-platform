import Header from "./components/Header.tsx";
import ToolBar, { type Draft } from "./components/ToolBar.tsx";
import LogTable from "./components/LogTable.tsx";
import Pager from "./components/Pager.tsx";
import { mockLogs } from "./mockLogs.ts";
import { useState } from "react";

function App() {
  const PAGE_SIZE = 20;
  const [page, setPage] = useState(1);
  // TODO: mock data for now; removed after GET services/envs are implemented
  const mockServiceList = ["Query API", "Ingestion API", "Worker", "RAG"];
  const mockEnvList = [
    "test",
    "development",
    "staging",
    "production",
    "very very very very long string",
  ];
  const handleSearch = (draft: Draft) => {
    console.log(`handle search! new filters: ${JSON.stringify(draft)}`);
  };
  const handlePrev = () => {
    setPage((prev) => prev - 1);
  };
  const handleNext = () => {
    setPage((prev) => prev + 1);
  };

  return (
    <main className="page">
      <Header logoText="cui" />
      <ToolBar
        serviceList={mockServiceList}
        envList={mockEnvList}
        onSearch={handleSearch}
      />
      <LogTable rowsPerPage={PAGE_SIZE} pageNum={page} logs={mockLogs} />
      <Pager
        pageNum={page}
        canPrev={page > 1}
        canNext={page !== Math.ceil(mockLogs.length / PAGE_SIZE)}
        onPrev={handlePrev}
        onNext={handleNext}
      />
    </main>
  );
}

export default App;
