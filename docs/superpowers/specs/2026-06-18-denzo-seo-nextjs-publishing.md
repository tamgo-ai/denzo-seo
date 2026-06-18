# DENZO SEO — Next.js App Router Publishing

## Overview

Extender DENZO SEO para publicar páginas como componentes nativos de Next.js App Router, además del formato HTML estático actual. El primer tenant objetivo es Auto Collision Group (`pdx-prog/acg-web`).

## Arquitectura de publicación

### Estructura de archivos generados

```
app/[locale]/[type]/[slug]/page.jsx
```

Ejemplos:
- `app/[locale]/services/collision-repair-near-me/page.jsx`
- `app/[locale]/locations/whittier-auto-body-shop/page.jsx`
- `app/[locale]/blog/insurance-claim-guide/page.jsx`

### ¿Por qué `[locale]`?

El repo usa `next-intl` con `[locale]` catch-all. Las páginas DENZO aceptan el param `locale` pero devuelven el mismo contenido en inglés para ambos (`en`/`es`). Esto evita errores de compilación y mantiene compatibilidad con el layout existente.

### Beneficios de estar bajo `app/[locale]/`

- Heredan `layout.jsx` → Navbar, Footer, FloatingPhoneCTA, GTM, UserWay automáticos
- Heredan `globals.css` (Tailwind) sin duplicar estilos
- URL limpia: `/en/services/oem-parts-repair` (sin `.html`)
- Middleware `next-intl` las maneja sin config adicional
- Compatibles con `sitemap.ts` existente

## La página generada (`page.jsx`)

Server Component (no `'use client'`) con:

1. **`metadata` export** — title, description, keywords, canonical, OpenGraph, Twitter card
2. **Contenido SEO** — `dangerouslySetInnerHTML` con clases Tailwind heredadas del layout
3. **Schema JSON-LD** — inyectado vía `<script>` al final del componente
4. **H1** — extraído del contenido o del `meta_title`
5. **Sin dependencia de `next-intl`** — contenido en inglés directo
6. **Sin imports de componentes** — autónomo, no acoplado a los 30+ componentes del repo

```jsx
// Ejemplo de page.jsx generado
export const metadata = {
  title: 'Collision Repair Near Me — Auto Collision Group',
  description: 'Certified collision repair...',
  alternates: { canonical: 'https://www.autoacg.com/en/services/collision-repair-near-me' },
  openGraph: { ... }
};

export default function CollisionRepairNearMe() {
  return (
    <div className="max-w-4xl mx-auto px-6 py-14 prose ...">
      <h1>Collision Repair Near Me</h1>
      <div dangerouslySetInnerHTML={{ __html: "<p>...</p>" }} />
      <script type="application/ld+json"
        dangerouslySetInnerHTML={{ __html: '{"@context":"https://schema.org",...}' }} />
    </div>
  );
}
```

## Protección de páginas existentes (NO TOCAR)

### Páginas manuales existentes en ACG Web

| Ruta | Tipo | Slug |
|------|------|------|
| `app/[locale]/services/auto-body-repair/page.jsx` | service | auto-body-repair |
| `app/[locale]/services/aluminum-repair/page.jsx` | service | aluminum-repair |
| `app/[locale]/services/refinish/page.jsx` | service | refinish |
| `app/[locale]/services/page.jsx` | service | (índice) |
| `app/[locale]/locations/[slug]/page.jsx` | location | 13 ubicaciones (dinámico) |
| `app/[locale]/locations/page.jsx` | location | (índice) |
| `app/[locale]/certifications/[slug]/page.jsx` | certification | 30+ marcas (dinámico) |
| `app/[locale]/certifications/page.jsx` | certification | (índice) |
| `app/[locale]/about-us/` | page | about-us |
| `app/[locale]/faq/` | page | faq |
| `app/[locale]/careers/` | page | careers |
| `app/[locale]/ccpa/` | page | ccpa |
| `app/[locale]/privacy-policy/` | page | privacy-policy |
| `app/[locale]/page.jsx` | page | (home) |
| `app/[locale]/layout.jsx` | layout | (layout raíz) |

### Mecanismo de protección

1. **Discovery inicial**: Script que recorre el repo vía GitHub API y popula `managed_paths` con `managed=0` para todas las rutas existentes
2. **Publisher check**: Antes de publicar, `_check_path_ownership()` verifica `managed_paths` — si `managed=0` → SKIP automático
3. **Auto-detección**: Si una ruta NO está en `managed_paths` pero existe en GitHub → se marca como `managed=0` (protegida)

## Cambios en DENZO SEO

### 1. `nextjs_renderer.py` v2
- **Ubicación**: `denzo/agents/layer4_publishing/nextjs_renderer.py`
- **Cambios**:
  - Eliminar hero/stats/gallery/CTA hardcodeados (el layout del repo ya tiene CTAs)
  - Simplificar a: metadata + H1 + contenido + schema
  - Usar solo clases Tailwind compatibles con `globals.css` del repo
  - Aceptar `nextjs_assets` settings para customización por tenant

### 2. `github_publisher.py`
- **Ubicación**: `denzo/agents/layer4_publishing/github_publisher.py`
- **Cambios**:
  - Path de publicación Next.js: `app/[locale]/[type]/[slug]/page.jsx`
  - Discovery inicial de rutas existentes (poblar `managed_paths`)
  - El resto de la lógica (quality gate, hash, velocity) se reutiliza

### 3. `run_acg_publisher.py`
- **Ubicación**: `scripts/run_acg_publisher.py`
- **Cambios**:
  - Actualizar `github_token` al token funcional de Raúl
  - Añadir paso de discovery previo a la publicación

### 4. Auto-detección de formato
- **Lógica**: Si `github_format` no está configurado → analizar `package.json` del repo
  - `"next"` en dependencies → `nextjs`
  - Sin `package.json` → `html`
- **UI en dashboard**: Selector `github_format` en Settings → Publisher Configuration

### 5. Regeneración de páginas
- Las 72 páginas `ready` actuales tienen contenido HTML genérico (formato antiguo)
- Se necesita regenerar con el Programmatic SEO agent apuntando al formato `nextjs`
- O: convertir el contenido existente (el HTML se puede reusar, solo cambia el wrapper)

## Plan de implementación

### Fase 1: Token + Discovery (1h)
1. Actualizar `github_token` en `client_context` para ACG
2. Script de discovery: escanear `app/[locale]/` y poblar `managed_paths`
3. Verificar que las páginas existentes quedan protegidas

### Fase 2: Next.js Renderer v2 (2-3h)
4. Reescribir `render_nextjs_page()` — simplificado, sin hero/stats/gallery
5. Probar con una página de ejemplo
6. Quality gate adaptado a JSX (no HTML)

### Fase 3: Publisher + Publicación (2h)
7. Actualizar `github_publisher.py` para paths Next.js
8. Ejecutar publisher con 1-2 páginas de prueba
9. Validar que compilan en el repo (check visual)

### Fase 4: Regeneración + Publicación completa (2h)
10. Actualizar Content Optimizer para output Next.js
11. Regenerar/convertir las 72 páginas
12. Publicar todas

### Fase 5: UI + Auto-detección (2h)
13. Selector `github_format` en dashboard
14. Auto-detección de formato
15. Documentación

## Notas

- **NO ejecutar Pipeline Director** (loop infinito, bug conocido)
- Usar scripts individuales controlados como `run_acg_publisher.py`
- El quality gate DEBE ejecutarse sobre el JSX final (no fragmentos crudos)
- Las páginas existentes son intocables — `managed=0` es la ley
