// Logo

type HeaderProps = {
  logoText: string;
};

const Header = (props: HeaderProps) => {
  return (
    <header className="headerbar">
      <a href="/" className="logo text-title">
        {props.logoText}
      </a>
    </header>
  );
};

export default Header;
