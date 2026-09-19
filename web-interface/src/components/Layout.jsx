import Navigation from "../Navigation.jsx";

function Layout({ children }) {
  return (
    <>
      <Navigation />

      <main className="page-content">
        {children}
      </main>
    </>
  );
}

export default Layout;