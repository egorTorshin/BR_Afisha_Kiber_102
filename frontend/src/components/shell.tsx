"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useRef, useState } from "react";
import { ArrowUpRight, Menu, X } from "lucide-react";
const links = [
  { href: "/", label: "Главная" },
  { href: "/events", label: "Афиша событий" },
  { href: "/agent", label: "Спросить агента" },
  { href: "/feedback", label: "Обратная связь" },
  { href: "/profile", label: "Мой профиль" },
];
export function Header() {
  const pathname = usePathname();
  const [open, setOpen] = useState(false);
  const menuButton = useRef<HTMLButtonElement>(null);
  return (
    <header
      className="site-header"
      onKeyDown={(event) => {
        if (event.key === "Escape" && open) {
          setOpen(false);
          menuButton.current?.focus();
        }
      }}
    >
      <div className="container header-inner">
        <Link href="/" className="brand">
          <span className="brand-mark" aria-hidden="true">
            ✳
          </span>
          <span>
            КУЛЬТУРА <span className="brand-sub">БАШКОРТОСТАНА</span>
          </span>
        </Link>
        <nav className="desktop-nav" aria-label="Основная навигация">
          {links.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              aria-current={pathname === link.href ? "page" : undefined}
            >
              {link.label}
            </Link>
          ))}
        </nav>
        <span className="region-label">
          Республика впечатлений <ArrowUpRight size={16} />
        </span>
        <button
          ref={menuButton}
          className="menu-button"
          aria-label={open ? "Закрыть меню" : "Открыть меню"}
          aria-expanded={open}
          aria-controls="mobile-nav"
          onClick={() => setOpen(!open)}
        >
          {open ? <X /> : <Menu />}
        </button>
      </div>
      {open && (
        <nav
          id="mobile-nav"
          className="mobile-nav container"
          aria-label="Мобильная навигация"
        >
          {links.map((link) => (
            <Link
              key={link.href}
              href={link.href}
              aria-current={pathname === link.href ? "page" : undefined}
              onClick={() => setOpen(false)}
            >
              {link.label}
            </Link>
          ))}
        </nav>
      )}
    </header>
  );
}
export function Footer() {
  return (
    <footer className="site-footer">
      <div className="container footer-inner">
        <Link className="brand" href="/">
          <span className="brand-mark" aria-hidden="true">
            ✳
          </span>
          <span>
            КУЛЬТУРА <span className="brand-sub">БАШКОРТОСТАНА</span>
          </span>
        </Link>
        <p>Афиша культурных событий республики</p>
        <Link href="/feedback">
          Написать нам <ArrowUpRight size={16} />
        </Link>
        <span className="footer-note">
          Афиша культуры Башкортостана
        </span>
      </div>
    </footer>
  );
}
