/**
 * Verificación del JWT de Cloudflare Access.
 *
 * Access pone el token en el header `Cf-Access-Jwt-Assertion` (y en la cookie
 * `CF_Authorization`) de cada petición que deja pasar. Se valida contra los certificados
 * públicos del team domain (`https://<team>/cdn-cgi/access/certs`), con el AUD de la
 * aplicación, el emisor `https://<team>` y la expiración.
 *
 * Un usuario trae el claim `email`. Un service token de Access (lo que usaría el motor de
 * la Mac) no trae email sino `common_name` = Client ID del token.
 */

import { type JWTPayload, createRemoteJWKSet, jwtVerify } from "jose";

export class AccessError extends Error {}

const jwksCache = new Map<string, ReturnType<typeof createRemoteJWKSet>>();

function jwks(teamDomain: string) {
  let set = jwksCache.get(teamDomain);
  if (!set) {
    // jose guarda las llaves en caché y solo vuelve a pedirlas si aparece un `kid` nuevo.
    set = createRemoteJWKSet(new URL(`https://${teamDomain}/cdn-cgi/access/certs`));
    jwksCache.set(teamDomain, set);
  }
  return set;
}

/** Limpia la caché de JWKS (pruebas). */
export function resetJwksCache(): void {
  jwksCache.clear();
}

export interface AccessConfig {
  teamDomain?: string | undefined;
  aud?: string | undefined;
}

export function normalizeTeamDomain(raw: string): string {
  return raw.replace(/^https?:\/\//, "").replace(/\/+$/, "");
}

export async function verifyAccessJwt(token: string, config: AccessConfig): Promise<JWTPayload> {
  if (!config.teamDomain || !config.aud) {
    throw new AccessError("Falta ACCESS_TEAM_DOMAIN o ACCESS_AUD en la configuración del Worker");
  }
  const team = normalizeTeamDomain(config.teamDomain);
  try {
    const { payload } = await jwtVerify(token, jwks(team), {
      audience: config.aud,
      issuer: `https://${team}`,
      requiredClaims: ["exp"],
    });
    return payload;
  } catch (err) {
    const reason = err instanceof Error ? err.message : String(err);
    throw new AccessError(`token de Access inválido: ${reason}`);
  }
}

/** El token de la petición: header de Access o, si no, la cookie CF_Authorization. */
export function accessToken(headers: Headers): string | null {
  const header = headers.get("Cf-Access-Jwt-Assertion");
  if (header) return header;
  const cookie = headers.get("Cookie") ?? "";
  const match = /(?:^|;\s*)CF_Authorization=([^;]+)/.exec(cookie);
  return match ? decodeURIComponent(match[1]!) : null;
}
