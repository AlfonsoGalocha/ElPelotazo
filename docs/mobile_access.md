# Acceso desde el móvil (despliegue en un servidor)

Pedido real de usuario: "me gustaría poder ver toda la app que tengo en el
móvil así no tendría que consultar al ordenador todo el rato". Se descartó
la opción de una VPN/túnel (Tailscale/Cloudflare Tunnel, que exigen tener
el ordenador encendido) a favor de un servidor propio, disponible 24/7 aunque
el ordenador de casa esté apagado.

Resumen de lo que monta esto: un servidor barato (Hetzner, ~4.5 €/mes) corre
el MISMO `docker-compose.yml` que ya usas en local, más un overlay
(`docker-compose.prod.yml`) que añade [Caddy](https://caddyserver.com/) como
único punto de entrada público -- consigue un certificado HTTPS real (Let's
Encrypt) él solo, sin tocar nginx/certbot a mano. Sin dominio propio: se usa
un subdominio gratuito de [sslip.io](https://sslip.io) que resuelve
automáticamente a la IP del servidor, sin registrar nada en ningún sitio.

## 1. Crear el servidor (Hetzner)

1. Crea una cuenta en [hetzner.com](https://www.hetzner.com/cloud/) (piden
   tarjeta, pero el CX22 son ~4.5 €/mes).
2. "Add Server" -> ubicación (Europa, la que esté más cerca) -> imagen
   **Ubuntu 24.04** -> tipo **CX22** (2 vCPU, 4 GB RAM -- suficiente para
   Postgres + backend + frontend + scheduler + los modelos de voz de Jarvis).
3. Añade tu clave SSH (o usa la contraseña que te den por email).
4. Anota la **IP pública** del servidor (algo como `95.216.xxx.xxx`) --
   la necesitas para el dominio del paso 3.

## 2. Conectarte y preparar el servidor

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

## 3. Clonar el repo y configurar `.env`

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
NEXT_PUBLIC_API_URL=https://95-216-xxx-xxx.sslip.io/backend-api
```

(cambia los guiones por los puntos reales de tu IP -- `95.216.1.2` ->
`95-216-1-2.sslip.io`, ese dominio ya resuelve a tu servidor sin que hagas
nada más).

**Sobre `AGENT_LLM_PROVIDER=claude_code`**: si lo usas, necesitas hacer
`claude login` DENTRO del contenedor del backend de este servidor (es una
sesión distinta a la de tu ordenador) -- `docker compose exec backend claude login`
tras el primer arranque. Si prefieres no repetir el login, usa
`AGENT_LLM_PROVIDER=anthropic` con una API key aquí.

## 4. Arrancar

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

## 5. Abrir desde el móvil

Abre `https://95-216-xxx-xxx.sslip.io` (tu dominio real) en el navegador
del móvil. En iOS/Android puedes "Añadir a pantalla de inicio" para que se
abra como una app, sin la barra de direcciones.

## Notas honestas

- **Primera vez con voz**: Whisper/Piper descargan sus modelos la primera
  vez que se usan (unos ~150-200 MB en total) -- la primera transcripción o
  respuesta hablada en el servidor nuevo tardará más mientras se descargan;
  quedan cacheados en `./data/cache/` para las siguientes veces.
- **Actualizar el código**: `git pull && docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build`
  (los mismos pasos de siempre, solo con los dos ficheros de compose).
- **Coste**: ~4.5 €/mes fijos mientras el servidor exista, independientemente
  de si lo usas o no -- a diferencia de Tailscale/Cloudflare Tunnel, que son
  gratis pero exigen el ordenador encendido.
- **El scheduler sigue corriendo en el servidor**, no en tu ordenador --
  una vez desplegado, puedes apagar tu máquina local sin que se corte nada.
