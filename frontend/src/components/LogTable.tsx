import { type LogItem } from "../types.ts";
import Badge from "./Badge.tsx";

type LogTableProps = {
  rowsPerPage: number;
  pageNum: number;
  logs: LogItem[];
};

const LogTable = (props: LogTableProps) => {
  return (
    <table className="log-table">
      <thead>
        <tr className="text-label">
          <th className="log-table__col--time">TIME</th>
          <th className="log-table__col--service">SERVICE</th>
          <th className="log-table__col--env">ENV</th>
          <th className="log-table__col--level">LEVEL</th>
          <th>MESSAGE</th>
        </tr>
      </thead>
      <tbody>
        {props.logs
          .slice(
            (props.pageNum - 1) * props.rowsPerPage,
            props.pageNum * props.rowsPerPage,
          )
          .map((log) => (
            <tr key={log.id} className="text-body">
              <td>
                {new Date(log.occurred_at).toLocaleString().replace(",", " ")}
              </td>
              <td title={log.service_name}>{log.service_name}</td>
              <td title={log.environment}>{log.environment}</td>
              <td>
                <Badge level={log.level} />
              </td>
              <td title={log.message}>{log.message}</td>
            </tr>
          ))}
      </tbody>
    </table>
  );
};

export default LogTable;
