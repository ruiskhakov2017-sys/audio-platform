import Script from 'next/script';

const ymId = process.env.NEXT_PUBLIC_YM_ID?.trim();
const gaId = process.env.NEXT_PUBLIC_GA_MEASUREMENT_ID?.trim();

/**
 * Подключает счётчики на всех страницах.
 * Работает только если заданы переменные окружения (см. .env.local.example).
 */
export function AnalyticsScripts() {
  return (
    <>
      {ymId ? (
        <Script id="ym-init" strategy="afterInteractive">
          {`(function(m,e,t,r,i,k,n){m[i]=m[i]||function(){(m[i].a=m[i].a||[]).push(arguments)};
m[i].l=1*new Date();
for (var j = 0; j < document.scripts.length; j++) { if (document.scripts[j].src === r) { return; } }
k=e.createElement(t),a=e.getElementsByTagName(t)[0],k.async=1,k.src=r,a.parentNode.insertBefore(k,a)})
(window, document, "script", "https://mc.yandex.ru/metrika/tag.js", "ym");
ym(${ymId}, "init", {
  clickmap:true,
  trackLinks:true,
  accurateTrackBounce:true,
  webvisor:true
});`}
        </Script>
      ) : null}

      {gaId ? (
        <>
          <Script
            strategy="afterInteractive"
            src={`https://www.googletagmanager.com/gtag/js?id=${encodeURIComponent(gaId)}`}
          />
          <Script id="ga-init" strategy="afterInteractive">
            {`window.dataLayer = window.dataLayer || [];
function gtag(){dataLayer.push(arguments);}
gtag('js', new Date());
gtag('config', ${JSON.stringify(gaId)}, { anonymize_ip: true });`}
          </Script>
        </>
      ) : null}
    </>
  );
}
