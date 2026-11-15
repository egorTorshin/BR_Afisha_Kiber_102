"use client";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { useRef, useState } from "react";
import { Menu, X } from "lucide-react";
import { BrandLockup } from "./brand-logo";
import { KuraiArt } from "./kurai-art";
import { BashkirOrnament, GeometricLandmark, type Landmark } from "./cultural-art";
const links = [
  { href: "/profile", label: "Профиль" },
  { href: "/submit-event", label: "Добавить событие" },
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
        <Link href="/" className="brand" aria-label="Афиша Башкортостан — главная"><BrandLockup /></Link>
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
  const places: Landmark[] = ["kurai", "friendship", "lale", "opera", "toratau", "shikhans"];
  return (
    <footer className="site-footer">
      <div className="container cultural-finale">
        <div className="cultural-finale-heading"><BashkirOrnament /></div>
        <div className="cultural-skyline" role="img" aria-label="Геометрическая панорама: курай, Монумент Дружбы, мечеть Ляля-Тюльпан, театр оперы и балета, Конгресс-холл Торатау и шиханы">{places.map(kind => <div className={`cultural-skyline-place cultural-skyline-${kind}`} key={kind}><GeometricLandmark kind={kind} /></div>)}</div>
      </div>
      <div className="container footer-inner">
        <div className="footer-kurai" aria-hidden="true"><KuraiArt /></div>
        <Link className="brand" href="/" aria-label="Афиша Башкортостан — главная"><BrandLockup /></Link>
        <nav aria-label="Ссылки в подвале"><Link href="/submit-event">Добавить событие</Link><Link href="/developers">Виджет</Link></nav>
        <p>Независимый навигатор по опубликованным событиям республики.</p>
        <Link className="footer-feedback" href="/feedback">Обратная связь ↗</Link>
      </div>
    </footer>
  );
}
