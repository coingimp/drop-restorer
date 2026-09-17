<?php
if (!defined('ABSPATH')) { exit; }

if (isset($GLOBALS['wp_query']) && is_object($GLOBALS['wp_query'])) {
    $GLOBALS['wp_query']->set_404();
}
status_header(404);
nocache_headers();

$site = dr_site();
$identity = isset($site['theme']) && is_array($site['theme']) ? $site['theme'] : array();
$domain = isset($identity['domain']) ? (string) $identity['domain'] : (string) wp_parse_url(home_url('/'), PHP_URL_HOST);
$hue = isset($identity['accent_hue']) ? (int) $identity['accent_hue'] : 218;
if ($hue < 0 || $hue > 359) { $hue = 218; }
$lang = strtolower(substr(isset($site['lang']) ? (string) $site['lang'] : 'en', 0, 2));
$copies = array(
    'cs' => array('title'=>'Tato stránka neexistuje', 'text'=>'Odkaz je pravděpodobně zastaralý nebo byla stránka přesunuta. Vraťte se na hlavní stránku a pokračujte odtud.', 'home'=>'Zpět na hlavní stránku'),
    'de' => array('title'=>'Diese Seite wurde nicht gefunden', 'text'=>'Der Link ist möglicherweise veraltet oder die Seite wurde verschoben. Kehren Sie zur Startseite zurück und setzen Sie Ihren Besuch dort fort.', 'home'=>'Zur Startseite'),
    'es' => array('title'=>'Esta página no existe', 'text'=>'Es posible que el enlace esté desactualizado o que la página se haya movido. Vuelve a la página principal para continuar.', 'home'=>'Volver al inicio'),
    'fr' => array('title'=>'Cette page est introuvable', 'text'=>'Le lien est peut-être obsolète ou la page a été déplacée. Revenez à la page d’accueil pour poursuivre votre visite.', 'home'=>'Retour à l’accueil'),
    'it' => array('title'=>'Questa pagina non esiste', 'text'=>'Il collegamento potrebbe essere obsoleto oppure la pagina è stata spostata. Torna alla pagina iniziale per continuare.', 'home'=>'Torna alla home'),
    'pl' => array('title'=>'Ta strona nie istnieje', 'text'=>'Link może być nieaktualny albo strona została przeniesiona. Wróć na stronę główną i kontynuuj przeglądanie.', 'home'=>'Wróć na stronę główną'),
    'ru' => array('title'=>'Такой страницы нет', 'text'=>'Возможно, ссылка устарела или страница была перемещена. Вернитесь на главную и продолжите просмотр сайта.', 'home'=>'Вернуться на главную'),
    'en' => array('title'=>'This page could not be found', 'text'=>'The link may be out of date or the page may have moved. Return to the home page and continue from there.', 'home'=>'Return to the home page'),
);
$copy = isset($copies[$lang]) ? $copies[$lang] : $copies['en'];
wp_enqueue_style('site-not-found', get_template_directory_uri() . '/assets/site-404.css', array(), '1.0');
get_header();
?>
<main class="site-not-found" style="--site-404-hue:<?php echo esc_attr((string) $hue); ?>">
    <section class="site-not-found__card" aria-labelledby="site-not-found-title">
        <div class="site-not-found__glow" aria-hidden="true"></div>
        <p class="site-not-found__domain"><?php echo esc_html($domain); ?></p>
        <p class="site-not-found__code" aria-hidden="true">404</p>
        <h1 id="site-not-found-title"><?php echo esc_html($copy['title']); ?></h1>
        <p class="site-not-found__text"><?php echo esc_html($copy['text']); ?></p>
        <a class="site-not-found__home" href="<?php echo esc_url(home_url('/')); ?>">
            <span aria-hidden="true">←</span> <?php echo esc_html($copy['home']); ?>
        </a>
    </section>
</main>
<?php get_footer(); ?>
