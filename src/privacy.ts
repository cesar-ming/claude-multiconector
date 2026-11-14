/**
 * Privacy guard: strips personal data from payloads before they reach the
 * model. Store data (orders, comments, users) routinely contains names,
 * emails, phone numbers and addresses that are not needed for marketing
 * analysis.
 */

const PII_KEYS = new Set(
  [
    "email",
    "phone",
    "firstname",
    "lastname",
    "name_of_customer",
    "address",
    "address1",
    "address2",
    "billingaddress",
    "shippingaddress",
    "defaultaddress",
    "zip",
    "postalcode",
    "street",
    "customer",
    "customeremail",
    "contactemail",
    "browserip",
    "clientip",
    "ip",
    "author_email",
    "author_ip",
    "author_user_agent",
    "useragent",
    "note",
    "cardnumber",
    "creditcard",
  ].map((k) => k.toLowerCase()),
);

function normaliseKey(key: string): string {
  return key.replace(/[_-]/g, "").toLowerCase();
}

const NORMALISED_PII = new Set([...PII_KEYS].map(normaliseKey));

const EMAIL_RE = /[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}/g;

export function scrubString(value: string): string {
  return value.replace(EMAIL_RE, "[email]");
}

/** Recursively removes PII keys and masks email addresses inside free text. */
export function stripPii<T>(value: T): T {
  if (Array.isArray(value)) return value.map((v) => stripPii(v)) as T;
  if (value && typeof value === "object") {
    const out: Record<string, unknown> = {};
    for (const [key, v] of Object.entries(value as Record<string, unknown>)) {
      if (NORMALISED_PII.has(normaliseKey(key))) continue;
      out[key] = stripPii(v);
    }
    return out as T;
  }
  if (typeof value === "string") return scrubString(value) as T;
  return value;
}
