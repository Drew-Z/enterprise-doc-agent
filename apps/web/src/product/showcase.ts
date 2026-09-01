export function isShowcaseMode(): boolean {
  return new URLSearchParams(window.location.search).get("showcase") === "1";
}
