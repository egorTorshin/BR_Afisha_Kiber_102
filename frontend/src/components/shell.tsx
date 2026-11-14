"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useRef, useState } from "react";
import { Menu, X } from "lucide-react";
const links = [
  { href: "/events", label: "Каталог" },
  { href: "/agent", label: "Поиск с помощником" },
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
          <span className="brand-mark" aria-hidden="true">А</span>
          <span>Афиша <span className="brand-sub">Башкортостана</span></span>
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
        <Link className="header-search" href="/events">Найти событие</Link>
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
        <Link className="brand" href="/"><span className="brand-mark" aria-hidden="true">А</span><span>Афиша <span className="brand-sub">Башкортостана</span></span></Link>
        <nav aria-label="Ссылки в подвале"><Link href="/events">Каталог</Link><Link href="/agent">Поиск с помощником</Link><Link href="/feedback">Обратная связь</Link><Link href="/developers">Виджет</Link></nav>
        <p>Независимый навигатор по опубликованным событиям республики.</p>
      </div>
    </footer>
  );
}
