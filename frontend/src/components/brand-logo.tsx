import Image from "next/image";

export function BrandLogo() {
  return <span className="culture-brand-mark" aria-hidden="true">
    <Image src="/culture-logo.jpg" alt="" width={1254} height={1254} sizes="84px" />
  </span>;
}

export function BrandLockup() {
  return <><BrandLogo /><span className="brand-title"><strong>Афиша</strong><span>Башкортостан</span></span></>;
}
