/* Swagger UI bootstrap for /api/docs (self-hosted, CSP-safe).
   CSP forbids inline scripts, so the one-line initialiser lives here. */
window.addEventListener("load", function () {
  window.ui = SwaggerUIBundle({
    url: "/api/openapi.json",
    dom_id: "#swagger-ui",
    layout: "BaseLayout",
    deepLinking: true,
    showExtensions: true,
    showCommonExtensions: true,
  });
});
