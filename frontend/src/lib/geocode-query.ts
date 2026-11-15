export function normalizedStreet(address: string, city: string): string {
  const cityName = city.trim().toLocaleLowerCase("ru");
  const parts = address.split(",").map((part) => part.trim()).filter(Boolean);
  const street = parts.filter((part) => {
    const lower = part.toLocaleLowerCase("ru").replace(/^г(?:ород)?\.?\s+/, "");
    return lower !== cityName && !/^\d{6}$/.test(part) &&
      !/^(?:россия|рф|(?:(?:респ\.?|республика)\s+)?башкортостан)$/i.test(part);
  }).join(", ");
  return street
    .replace(/(^|[\s,])(?:ул\.|ул(?=\s))\s*/giu, "$1улица ")
    .replace(/(^|[\s,])(?:пр-кт\.|пр-кт(?=\s))\s*/giu, "$1проспект ")
    .replace(/(^|[\s,])(?:просп\.|просп(?=\s))\s*/giu, "$1проспект ")
    .replace(/(^|[\s,])(?:пр\.|пр(?=\s))\s*/giu, "$1проспект ")
    .replace(/(^|[\s,])д\.?\s*(?=\d)/giu, "$1")
    .replace(/\s+/g, " ").trim();
}

export function geocodeQuery(address: string, city: string): string | null {
  const street = normalizedStreet(address, city);
  return street && city.trim() ? [street, city.trim(), "Республика Башкортостан", "Россия"].join(", ") : null;
}
