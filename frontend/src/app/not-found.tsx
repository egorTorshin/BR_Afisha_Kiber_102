import Link from "next/link";
export default function NotFound() {
  return (
    <div className="container page-content state">
      <p className="eyebrow">404 · СТРАНИЦА НЕ НАЙДЕНА</p>
      <h1>Страница не найдена</h1>
      <p>Событие или страница не найдены. В афише можно продолжить поиск.</p>
      <Link className="button" href="/events">
        Вернуться к афише
      </Link>
    </div>
  );
}
