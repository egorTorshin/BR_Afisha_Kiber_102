export function CategoryIllustration({ tone }: { tone: string }) {
  return <svg className="category-illustration" viewBox="0 0 32 32" fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="miter" aria-hidden="true" focusable="false">
    {tone === "music" ? <><path d="M12 7v15m12-18v16M12 7l12-3"/><path d="M12 22c-2 3-6 4-8 2s1-5 8-5m12 1c-2 3-6 4-8 2s1-5 8-5"/></> :
      tone === "theatre" ? <><path d="M5 6h22l-2 17-9 5-9-5L5 6Z"/><path d="M9 13h5m4 0h5M11 19l5 3 5-3"/></> :
      tone === "exhibition" ? <><path d="M5 5h22v22H5zM9 9h14v14H9z"/><path d="m12 20 4-5 4 5"/></> :
      tone === "festival" ? <><path d="M4 8h24M7 8l4 9 5-9 5 9 4-9M6 25h20"/><path d="m12 21 4-4 4 4"/></> :
      tone === "cinema" ? <><path d="M5 6h22v20H5zM10 6v20m12-20v20M5 12h5m-5 8h5m12-8h5m-5 8h5"/></> :
      tone === "excursion" ? <><path d="m4 14 12-9 12 9M7 14v13h18V14M12 27V16h8v11"/></> :
      <><path d="m16 4 12 12-12 12L4 16 16 4Z"/><path d="m16 10 6 6-6 6-6-6 6-6Z"/></>}
  </svg>;
}
