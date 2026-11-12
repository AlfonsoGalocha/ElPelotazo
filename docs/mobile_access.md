# Acceso desde el móvil

Pedido real de usuario: "me gustaría poder ver toda la app que tengo en el
móvil así no tendría que consultar al ordenador todo el rato". Dos opciones
con un trade-off real y explícito, no una "mejor que la otra":

- **Cloudflare Tunnel (gratis)**: tu ordenador tiene que estar encendido y
  con Docker corriendo para que la app sea accesible. Cero coste, cero
  configuración de red (ni router ni firewall).
- **Servidor propio (Hetzner, ~4.5 €/mes)**: accesible 24/7 aunque tu
  ordenador esté apagado. Tiene un coste mensual fijo.

Se descartó compartir el dominio/servidor de otra app existente (ElPlaybookNFL)
por riesgo de romperla sin necesidad -- son proyectos independientes.

## Opción gratis: Cloudflare Tunnel

`cloudflared` hace una conexión SALIENTE desde tu ordenador hacia Cloudflare
(como cualquier cliente HTTPS normal) y Cloudflare te da una URL pública
(`https://palabras-al-azar.trycloudflare.com`) que reenvía el tráfico hacia
tu app. Como es una conexión saliente, **no hace falta abrir ningún puerto
en el router ni tocar el firewall** -- la parte más simple de las dos
opciones.

Contrapartida real: es un "quick tunnel" (no hace falta ni cuenta de
Cloudflare ni dominio propio), y por eso la URL es EFÍMERA -- cambia cada
vez que el contenedor `cloudflared` se reinicia. Mientras lo dejes corriendo
sin reiniciar, la URL se mantiene estable.

### 1. Configurar `.env`

```bash
cp .env.example .env   # si no lo has hecho ya
```

Descomenta esta línea en tu `.env`:

```
NEXT_PUBLIC_API_URL=/backend-api
```

(Es una ruta RELATIVA a propósito, sin dominio -- así el navegador la
resuelve contra la URL efímera que sea en cada momento, sin tener que
reconstruir el frontend cada vez que cambia.)

### 2. Arrancar

```bash
docker compose -f docker-compose.yml -f docker-compose.tunnel.yml up -d --build
```

### 3. Consultar tu URL pública

```bash
docker compose -f docker-compose.yml -f docker-compose.tunnel.yml logs cloudflared | grep trycloudflare
```

Busca una línea con `https://algo-al-azar.trycloudflare.com` -- esa es tu
URL. Ábrela en el móvil y "Añadir a pantalla de inicio" para que se abra
como una app.

### Notas honestas (Cloudflare Tunnel)

- **La URL cambia** si reinicias `cloudflared` (`docker compose restart
  cloudflared`, un reinicio del ordenador, etc.) -- vuelve a mirar los logs
  cada vez que pase.
- **Tu ordenador tiene que estar encendido y con Docker corriendo** -- si lo
  apagas, la app deja de ser accesible desde el móvil (aunque sigas en la
  misma WiFi de casa).
- Al ser una URL pública real (cualquiera con el link puede abrirla, no solo
  tú), si te preocupa que alguien más la encuentre, dilo y añadimos una
  contraseña (Cloudflare Access, o autenticación básica en el `Caddyfile.tunnel`).
- **Primera vez con voz**: Whisper/Piper descargan sus modelos la primera
  vez que se usan (~150-200 MB) -- la primera transcripción/respuesta
  hablada tardará más; quedan cacheados en `./data/cache/` después.

## Opción de pago: servidor propio (24/7)

Si en algún momento prefieres que la app este disponible aunque tu ordenador
esté apagado, un servidor barato (Hetzner, ~4.5 €/mes) corre el MISMO
`docker-compose.yml` que ya usas en local, más un overlay
(`docker-compose.prod.yml`) que añade [Caddy](https://caddyserver.com/) como
único punto de entrada público -- consigue un certificado HTTPS real (Let's
Encrypt) él solo. Sin dominio propio: un subdominio gratuito de
[sslip.io](https://sslip.io) resuelve automáticamente a la IP del servidor,
sin registrar nada.

### 1. Crear el servidor (Hetzner)

1. Crea una cuenta en [hetzner.com](https://www.hetzner.com/cloud/) (piden
   tarjeta).
2. "Add Server" -> ubicación (Europa, la más cercana) -> imagen **Ubuntu
   24.04** -> plan **Regular Performance**, el tamaño más pequeño/barato
   (2 vCPU, 4 GB RAM -- conocido como CX22, suficiente para Postgres +
   backend + frontend + scheduler + los modelos de voz de Jarvis).
3. Añade tu clave SSH (o usa la contraseña que te den por email).
4. Anota la **IP pública** del servidor (algo como `95.216.xxx.xxx`).

### 2. Conectarte y preparar el servidor

```bash
ssh root@<IP_DEL_SERVIDOR>

# Instala Docker (script oficial, instala Engine + Compose plugin)
curl -fsSL https://get.docker.com | sh

# Firewall: SOLO SSH, HTTP y HTTPS accesibles desde internet -- ni el
# backend (8000) ni el frontend (3000) deben ser alcanzables directamente,
# solo a traves de Caddy (que habla HTTPS). Postgres ya no expone puerto
# al host en absoluto (ver docker-compose.yml), no hace falta bloquearlo.
apt-get install -y ufw
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw --force enable
```

### 3. Clonar el repo y configurar `.env`

```bash
git clone https://github.com/AlfonsoGalocha/ElPelotazo.git
cd ElPelotazo
cp .env.example .env
nano .env   # o vim/vi
```

Rellena en `.env` (además de lo que ya tenías en local -- `ODDS_API_KEY`,
`AGENT_ENABLED`, `AGENT_SHARED_SECRET`, etc.):

```
DOMAIN=95-216-xxx-xxx.sslip.io
NEXT_PUBLIC_API_URL=/backend-api
```

(cambia los guiones por los puntos reales de tu IP -- `95.216.1.2` ->
`95-216-1-2.sslip.io`, ese dominio ya resuelve a tu servidor sin que hagas
nada más. `NEXT_PUBLIC_API_URL` relativo funciona igual que en la opción
gratis: Caddy sirve frontend y backend bajo el mismo dominio.)

**Sobre `AGENT_LLM_PROVIDER=claude_code`**: si lo usas, necesitas hacer
`claude login` DENTRO del contenedor del backend de este servidor (es una
sesión distinta a la de tu ordenador) -- `docker compose exec backend claude login`
tras el primer arranque. Si prefieres no repetir el login, usa
`AGENT_LLM_PROVIDER=anthropic` con una API key aquí.

### 4. Arrancar

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build
```

La primera vez tarda un rato (build de las 3 imágenes + Caddy pidiendo el
certificado HTTPS). Comprueba que el certificado se emitió bien:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs caddy
```

Busca una línea `certificate obtained successfully`. Si sale un error de
tipo "connection refused" al pedir el certificado, casi siempre es que el
firewall (paso 2) no dejaba pasar el puerto 80/443 todavía -- revísalo y
reinicia Caddy (`docker compose restart caddy`).

### 5. Abrir desde el móvil

Abre `https://95-216-xxx-xxx.sslip.io` (tu dominio real) en el navegador
del móvil. "Añadir a pantalla de inicio" para que se abra como una app.

### Notas honestas (servidor propio)

- **Primera vez con voz**: mismo aviso que arriba (Whisper/Piper descargan
  modelos la primera vez).
- **Actualizar el código**: `git pull && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`.
- **Coste**: ~4.5 €/mes fijos mientras el servidor exista, uses la app o no.
- **El scheduler corre en el servidor**, no en tu ordenador -- puedes
  apagar tu máquina local sin que se corte nada.
