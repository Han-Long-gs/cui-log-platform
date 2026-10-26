
type PagerProps = {
    pageNum: number;
    canPrev: boolean;
    canNext: boolean;
    onPrev: () => void;
    onNext: () => void
}

const Pager = (props: PagerProps) => {
  return (
    <div className="pager">
        <button className="btn btn--secondary text-body" onClick={props.onPrev}>← Prev</button>
        <span className="text-body">Page {props.pageNum}</span>
        <button className="btn btn--secondary text-body" onClick={props.onNext}>Next →</button>
    </div>
  )
}

export default Pager