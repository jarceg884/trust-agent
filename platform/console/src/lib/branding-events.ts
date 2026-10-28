/**
 * Evento de cliente "branding publicado cambió" (spec 028 D-028-09,
 * t_42e23b01).
 *
 * Publish/rollback desde /branding lo emite; el TenantBrand del sidebar
 * lo escucha y re-fetcha GET /api/branding → product_name, logo, favicon
 * y <title> del shell se actualizan SIN reload (REQ-BRD2-008).
 *
 * CustomEvent sobre window en vez de un bus con estado: los dos extremos
 * viven en el mismo document, cero dependencias, cero estado global que
 * pueda divergir del servidor.
 */

export const BRANDING_UPDATED_EVENT = "trust:branding-updated";

/** Emite el evento (client-side; no-op en SSR por seguridad). */
export function notifyBrandingUpdated(): void {
  if (typeof window === "undefined") return;
  window.dispatchEvent(new CustomEvent(BRANDING_UPDATED_EVENT));
}

/** Suscribe un listener; devuelve el unsubscribe. */
export function onBrandingUpdated(listener: () => void): () => void {
  window.addEventListener(BRANDING_UPDATED_EVENT, listener);
  return () => window.removeEventListener(BRANDING_UPDATED_EVENT, listener);
}
