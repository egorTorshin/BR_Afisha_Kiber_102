export type Landmark = "friendship" | "lale" | "opera" | "toratau" | "shikhans" | "kurai";

export function GeometricLandmark({ kind, className = "" }: { kind: Landmark; className?: string }) {
  return <svg className={className} viewBox="0 0 320 200" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false">
    {kind === "friendship" && <>
      <path d="M137 22h13l5 126h-29L137 22Zm33 0h13l11 126h-29l5-126Z" fill="currentColor" />
      <path d="M126 59h68v8h-68zm-1 34h70v8h-70zm-3 34h74v8h-74z" fill="var(--art-accent, #D5A547)" />
      <path d="M103 148h114v16H103zm-22 16h158v14H81zm-26 14h210v10H55z" fill="currentColor" />
      <path d="m92 145 14-22 13 8 6 17H92Zm136 0-14-22-13 8-6 17h33Z" fill="var(--art-secondary, #B83D35)" />
    </>}
    {kind === "lale" && <>
      <path d="M63 61h22v105H63zm172 0h22v105h-22z" fill="currentColor" />
      <path d="m51 35 13 12 10-22 12 22 13-12-9 35H60l-9-35Zm170 0 13 12 10-22 12 22 13-12-9 35h-30l-9-35Z" fill="var(--art-secondary, #B83D35)" />
      <path d="M39 166h242v19H39zM94 107h132l-11 59H105l-11-59Z" fill="currentColor" />
      <path d="m94 107 66-36 66 36H94Z" fill="var(--art-secondary, #B83D35)" />
      <path d="M146 136h28v30h-28z" fill="var(--art-cutout, #103F32)" />
    </>}
    {kind === "opera" && <>
      <path d="M28 104h264v70H28zM53 91h214v13H53z" fill="currentColor" />
      <path d="m124 74 9-29 27-15 27 15 9 29h-72Zm34-55h4v14h-4z" fill="var(--art-accent, #D5A547)" />
      <path d="M91 74h138l22 17H69l22-17Z" fill="currentColor" />
      <path d="M80 106h160v12H80zm8 48h144v8H88z" fill="var(--art-secondary, #B83D35)" />
      <path d="M94 118h12v36H94zm40 0h12v36h-12zm40 0h12v36h-12zm40 0h12v36h-12z" fill="var(--art-cutout, #103F32)" />
      <path d="M18 174h284v12H18z" fill="currentColor" />
    </>}
    {kind === "toratau" && <>
      <path d="M19 92 69 69 121 56 185 50 251 59 301 83v91H19V92Z" fill="currentColor" />
      <path d="m32 100 55-24 64-12 83 7 54 20v68H32v-59Z" fill="var(--art-secondary, #1769A4)" />
      <path d="M73 83v76m44-90v90m45-95v95m45-92v92m42-82v82M32 119h256" stroke="var(--art-cutout, #103F32)" strokeWidth="5" />
      <path d="M16 174h288v12H16z" fill="currentColor" />
    </>}
    {kind === "shikhans" && <>
      <path d="M5 160 44 133 81 128 113 145 143 124 165 82 190 64 215 78 243 130 276 124 315 153v34H5v-27Z" fill="currentColor" />
      <path d="m165 82 25-18 25 14 17 34-34-12-24 20-24 4 15-42Z" fill="var(--art-secondary, #1769A4)" />
      <path d="M5 181h310" stroke="var(--art-accent, #D5A547)" strokeWidth="5" />
    </>}
    {kind === "kurai" && <>
      <path d="m38 152 28-12L269 45l15 14L81 154l-28 12-15-14Z" fill="currentColor" />
      <path d="m93 132 6 12m31-29 6 12m31-29 6 12m31-29 6 12m31-29 6 12" stroke="var(--art-cutout, #103F32)" strokeWidth="6" />
      <path d="m275 38 15 14-6 7-15-14 6-7Z" fill="var(--art-accent, #D5A547)" />
    </>}
  </svg>;
}

export function BashkirOrnament({ className = "" }: { className?: string }) {
  return <svg className={className} viewBox="0 0 320 48" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false">
    <path d="M0 15h48l20 18 20-18 20 18 20-18h64l20 18 20-18 20 18 20-18h48" stroke="currentColor" strokeWidth="5" />
    <path d="m160 5 19 19-19 19-19-19 19-19Z" fill="var(--ornament-accent, #B83D35)" />
    <path d="m160 14 10 10-10 10-10-10 10-10Z" fill="var(--ornament-cutout, #F7F5EF)" />
  </svg>;
}

export function BashkirCorner({ className = "" }: { className?: string }) {
  return <svg className={className} viewBox="0 0 80 80" fill="none" xmlns="http://www.w3.org/2000/svg" aria-hidden="true" focusable="false">
    <path d="M4 4h72v12H16v60H4V4Zm24 24h48v10H38v38H28V28Z" fill="currentColor" />
    <path d="m57 46 16 16-16 16-16-16 16-16Z" fill="var(--ornament-accent, #B83D35)" />
    <path d="m57 54 8 8-8 8-8-8 8-8Z" fill="var(--ornament-cutout, #F7F5EF)" />
  </svg>;
}
