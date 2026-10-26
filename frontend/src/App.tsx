import Badge from "./components/Badge.tsx"
import Header from "./components/Header.tsx"
import Pager from "./components/Pager.tsx"

function App() {
  function testOnNext() {
    console.log("onNext called!")
  }

  function testOnPrev() {
    console.log("onPrev called!")
  }

  return (
    <>
      <Header logoText="cui"/>
      <Badge level="INFO"/>
      <Badge level="ERROR"/>
      <Badge level="WARNING"/>
      <Badge level="DEBUG"/>
      <Badge level="CRITICAL"/>
      <Pager pageNum={1} canPrev={true} canNext={true} onPrev={testOnPrev} onNext={testOnNext}/>
    </>
  )
}

export default App
