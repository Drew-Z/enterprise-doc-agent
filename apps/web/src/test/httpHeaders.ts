/** Match HTTP header values independently of RequestInit's allowed representations. */
export function headersContaining(expected: Record<string, string>) {
  return {
    asymmetricMatch(actual: HeadersInit): boolean {
      const headers = new Headers(actual);
      return Object.entries(expected).every(([key, value]) => headers.get(key) === value);
    },
    toString: () => "HeadersContaining",
  };
}
