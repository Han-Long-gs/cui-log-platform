type PagerProps = {
  pageNum: number;
  canPrev: boolean;
  canNext: boolean;
  onPrev: () => void;
  onNext: () => void;
};

const Pager = (props: PagerProps) => {
  return (
    <div className="pager">
      <button
        className="btn btn--secondary text-body"
        onClick={props.onPrev}
        disabled={!props.canPrev}
      >
        ← Prev
      </button>
      <span className="text-label pager__page">Page {props.pageNum}</span>
      <button
        className="btn btn--secondary text-body"
        onClick={props.onNext}
        disabled={!props.canNext}
      >
        Next →
      </button>
    </div>
  );
};

export default Pager;
