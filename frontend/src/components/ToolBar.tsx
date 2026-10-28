import { useState } from "react";
import { LEVELS } from "../types.ts";

type ToolBarProps = {
  serviceList: Array<string>;
  envList: Array<string>;
  onSearch: (draft: Draft) => void;
};

export type Draft = {
  service: string;
  env: string;
  level: string;
};

const ToolBar = (props: ToolBarProps) => {
  const [draft, setDraft] = useState({ service: "", env: "", level: "" });

  return (
    <div className="toolbar">
      <label className="select-field text-label">
        Service
        <input
          name="service-filter"
          list="service-options"
          placeholder="All"
          className="text-body select-field__box"
          value={draft.service}
          onChange={(e) => setDraft({ ...draft, service: e.target.value })}
        />
        <datalist id="service-options">
          {props.serviceList.map((service: string) => (
            <option key={service}>{service}</option>
          ))}
        </datalist>
      </label>

      <label className="select-field text-label">
        Environment
        <input
          name="env-filter"
          list="env-options"
          placeholder="All"
          className="text-body select-field__box"
          value={draft.env}
          onChange={(e) => setDraft({ ...draft, env: e.target.value })}
        />
        <datalist id="env-options">
          {props.envList.map((env: string) => (
            <option key={env}>{env}</option>
          ))}
        </datalist>
      </label>

      <label className="select-field text-label">
        Level
        <select
          name="level-filter"
          className="text-body select-field__box"
          value={draft.level}
          onChange={(e) => setDraft({ ...draft, level: e.target.value })}
        >
          <option value="">All</option>
          {LEVELS.map((level: string) => (
            <option key={level}>{level}</option>
          ))}
        </select>
      </label>

      <button
        className="btn btn--primary text-body"
        onClick={() => props.onSearch(draft)}
      >
        Search
      </button>
    </div>
  );
};

export default ToolBar;
