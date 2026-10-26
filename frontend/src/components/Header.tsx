// Logo

type HeaderProps = {
    logoText: string
}

const Header = (props: HeaderProps) => {
  return (
    <a href="/" className="logo text-title">{props.logoText}</a>
  )
}

export default Header