<?php
if (!defined('ABSPATH')) { exit; }

function dr_widget_offers() {
    $offers = get_option('dr_affiliate_offers',array());
    return is_array($offers) ? $offers : array();
}
function dr_widget_register_offers($offers,$table_id,$route) {
    $registry=dr_widget_offers();$updated=$registry;
    foreach($updated as $id=>$row) {
        if((int)$row['table_id']===(int)$table_id && $row['route']===$route) { unset($updated[$id]); }
    }
    foreach($offers as $row) { $updated[$row['id']]=$row; }
    if($updated!=$registry) { update_option('dr_affiliate_offers',$updated,false); }
}
function dr_widget_active_tables() {
    $active=array();
    foreach(dr_site()['pages'] as $key=>$page) {
        if(empty($page['casino'])) { continue; }
        $post=dr_owned_post($key);
        if($post && preg_match_all('/\[(?:casino_table|hds_table)\s+[^\]]*id=["\']?(\d+)/',$post->post_content,$ids)) {
            $active[$page['route']]=array_map('intval',$ids[1]);
        }
    }
    return $active;
}
function dr_widget_resolve_offer($offers,$alias,$referer='') {
    if (!preg_match('/^[a-z0-9]+(?:[.-][a-z0-9]+)*$/',$alias)) { return false; }
    $rows=array();$contextual=array();$ref=wp_parse_url($referer);$base=wp_parse_url(home_url('/'));
    $same=is_array($ref) && isset($ref['scheme'],$ref['host']) && $ref['scheme']===$base['scheme']
        && strtolower($ref['host'])===strtolower($base['host']) && ($ref['port'] ?? null)===($base['port'] ?? null)
        && !isset($ref['user']) && !isset($ref['pass']);
    $active=dr_widget_active_tables();
    foreach($offers as $offer) {
        if($offer['alias']!==$alias || time()-(int)$offer['seen_at']>=DAY_IN_SECONDS
            || !in_array((int)$offer['table_id'],$active[$offer['route']] ?? array(),true)) { continue; }
        $rows[]=$offer;
        if($same && $offer['route']===($ref['path'] ?? '/')) { $contextual[]=$offer; }
    }
    if($contextual) { $rows=$contextual; }
    $targets=array();foreach($rows as $row) { $targets[$row['target']]=true; }
    if(count($targets)===1) { return $rows[0]; }
    if($contextual || !$rows) { return false; }
    // A direct visit without page context may reuse the same known campaign.
    // Only pg may differ; the selected destination itself is never rewritten.
    $signatures=array();
    foreach($rows as $row) {
        $target=wp_parse_url($row['target']);
        if(($target['scheme'] ?? '')!=='https' || ($target['host'] ?? '')!=='gmbl.guru' || ($target['path'] ?? '')!=='/lldrp'
            || isset($target['port']) || isset($target['user']) || isset($target['pass']) || !empty($row['context_issues'])) { return false; }
        $parameters=dr_widget_parameters($target['query'] ?? '');$signature=array();$pg=0;
        foreach($parameters as $pair) { if($pair[0]==='pg') { $pg++; } else { $signature[]=$pair; } }
        if($pg!==1) { return false; }
        $signatures[wp_json_encode(array($target['fragment'] ?? '',$signature))]=true;
    }
    if(count($signatures)!==1) { return false; }
    usort($rows,static function($a,$b) { return array($a['route'],$a['table_id'],$a['id']) <=> array($b['route'],$b['table_id'],$b['id']); });
    return $rows[0];
}
function dr_widget_target_valid($url) {
    return is_string($url) && strpos($url,'https://') === 0 && !preg_match('/[\s\\\\\x00-\x1f\x7f]|%0[ad]/i',$url) && wp_http_validate_url($url);
}
function dr_widget_binding_valid($offer) {
    $resolved=dr_widget_resolve_offer(dr_widget_offers(),$offer['alias'],home_url($offer['route']));
    return $resolved && $resolved['target']===$offer['target'];
}
function dr_widget_parameters($query) {
    $pairs=array();
    foreach(explode('&',(string)$query) as $part) {
        if($part==='') { continue; }
        $pair=explode('=',$part,2);$pairs[]=array(urldecode($pair[0]),isset($pair[1]) ? urldecode($pair[1]) : '');
    }
    return $pairs;
}
function dr_widget_parameter_issues($parameters,$context,$target,$alias,$route) {
    $tracking=wp_parse_url($target,PHP_URL_HOST)==='gmbl.guru' && wp_parse_url($target,PHP_URL_PATH)==='/lldrp';
    $mapping=$tracking ? array('dept'=>'d','st'=>'st','p'=>'pr') : array('dept'=>'dept','st'=>'st','p'=>'p');
    $expected=dr_widget_parameters($context['sfx']);$issues=array();
    foreach($mapping as $key=>$mapped) { if($context[$key]!=='') { $expected[]=array($mapped,$context[$key]); } }
    if($tracking) { $host=wp_parse_url(dr_site()['origin'],PHP_URL_HOST);$expected[]=array('br',$alias);$expected[]=array('s',$host);$expected[]=array('pg',$host.$route); }
    foreach($expected as $pair) {
        $values=array();foreach($parameters as $candidate) { if($candidate[0]===$pair[0]) { $values[]=$candidate[1]; } }
        $matches=$values===array($pair[1]);
        if($tracking && $pair[0]==='st') { $matches=count($values)===1 && strtolower($values[0])===strtolower($pair[1]); }
        if(!$matches) { $issues[]='Не подтверждён параметр таблицы/страницы: '.$pair[0]; }
    }
    if($tracking) { foreach(array('net','g') as $key) {
        $values=array();foreach($parameters as $pair) { if($pair[0]===$key) { $values[]=$pair[1]; } }
        if(count($values)!==1 || $values[0]==='') { $issues[]='Отсутствует или неоднозначен параметр: '.$key; }
    } }
    return $issues;
}
function dr_widget_parse($html,$table_id,$route) {
    if (!class_exists('DOMDocument')) { return new WP_Error('dr_dom','Для таблицы требуется PHP DOM/XML.'); }
    $old = libxml_use_internal_errors(true); $dom = new DOMDocument();
    // Explicit body keeps HTML5 section fragments out of libxml's inferred head.
    $dom->loadHTML('<?xml encoding="UTF-8"><html><body>' . preg_replace('/^\xEF\xBB\xBF/', '', $html) . '</body></html>', LIBXML_NONET | LIBXML_NOERROR | LIBXML_NOWARNING);
    libxml_clear_errors();libxml_use_internal_errors($old);$xpath = new DOMXPath($dom);
    $links = $xpath->query('//a[contains(concat(" ",normalize-space(@class)," ")," js-go-link ")][@data-slug]');
    if (!$links->length) { return new WP_Error('dr_table','В ответе провайдера нет распознанных партнёрских ссылок.'); }
    $roots=$xpath->query('//*[contains(concat(" ",normalize-space(@class)," ")," casino-list ") or @data-style="folders" or @data-style="offer_wall"]');
    $context=array();foreach(array('dept','st','p','sfx') as $key) { $context[$key]=$roots->length ? $roots->item(0)->getAttribute('data-'.$key) : ''; }
    $offers = array(); $registry = dr_widget_offers();
    foreach ($links as $link) {
        $alias = strtolower(trim($link->getAttribute('data-slug')));
        $target = $link->getAttribute('data-direct-url') ?: $link->getAttribute('href');
        if (!preg_match('/^[a-z0-9]+(?:[.-][a-z0-9]+)*$/',$alias) || !dr_widget_target_valid($target)) { return new WP_Error('dr_offer','Некорректный алиас или целевая ссылка партнёра.'); }
        $parameters=dr_widget_parameters(wp_parse_url($target,PHP_URL_QUERY));$issues=dr_widget_parameter_issues($parameters,$context,$target,$alias,$route);
        $brand = trim($link->getAttribute('data-brand')); $images = $link->getElementsByTagName('img');
        if (!$brand && $images->length) { $brand = trim($images->item(0)->getAttribute('alt')); }
        $labels=$link->getElementsByTagName('p');if(!$brand && $labels->length) { $brand=trim($labels->item(0)->textContent); }
        if (!$brand) { $brand = $alias; }
        $id = substr(hash('sha256',$table_id . "\n" . dr_site()['origin'] . $route . "\n" . $alias . "\n" . $target),0,24);
        $verified = !$issues && isset($registry[$id]) && $registry[$id]['target'] === $target && !empty($registry[$id]['verified']);
        $row = array('id'=>$id,'table_id'=>$table_id,'route'=>$route,'alias'=>$alias,'brand'=>$brand,'target'=>$target,'parameters'=>$parameters,'context'=>$context,'context_issues'=>$issues,'seen_at'=>time(),'verified'=>$verified);
        if (isset($registry[$id]['check']) && $registry[$id]['target'] === $target) { $row['check']=$registry[$id]['check']; }
        if (isset($offers[$id]) && $offers[$id]['brand'] !== $alias) { $row['brand']=$offers[$id]['brand']; }
        $offers[$id]=$row;
        $link->setAttribute('href',home_url('/go/' . rawurlencode($alias)));
        $link->setAttribute('rel','nofollow sponsored noopener');
        $link->setAttribute('referrerpolicy','same-origin');
        $link->removeAttribute('data-direct-url');
        $link->setAttribute('data-dr-offer',$id);
    }
    // Remove whole style nodes: wp_kses removes their tags but leaves raw CSS
    // visible in the article. Presentation is loaded by dr_widget_styles().
    foreach (array('script','style','iframe','object','embed','form','base','meta','link') as $tag) {
        $nodes=$dom->getElementsByTagName($tag); while($nodes->length) { $nodes->item(0)->parentNode->removeChild($nodes->item(0)); }
    }
    $all=$xpath->query('//*');
    foreach ($all as $node) {
        $remove=array();foreach($node->attributes as $attribute) { if (stripos($attribute->name,'on') === 0) { $remove[]=$attribute->name; } }
        foreach($remove as $attr) { $node->removeAttribute($attr); }
        if ($node->tagName==='a' && !$node->getAttribute('data-dr-offer') && strpos($node->getAttribute('href'),'#')!==0) { $node->removeAttribute('href'); }
    }
    // Replace this table/page snapshot, so an updated offer cannot leave a
    // second historical destination attached to the same clean brand URL.
    dr_widget_register_offers($offers,$table_id,$route);
    $body=$dom->getElementsByTagName('body')->item(0);$output='';
    if ($body) { foreach($body->childNodes as $child) { $output.=$dom->saveHTML($child); } }
    $allowed=wp_kses_allowed_html('post');
    foreach($allowed as $tag=>&$attributes) { $attributes['data-*']=true; $attributes['aria-*']=true; }
    unset($attributes);
    $allowed['a']['referrerpolicy']=true;
    return array('html'=>wp_kses($output,$allowed),'offers'=>array_values($offers),'table_id'=>$table_id,'route'=>$route,'source_hash'=>hash('sha256',$html),'at'=>time());
}
function dr_widget_data($table_id,$route,$refresh=false) {
    if ($table_id<1 || $table_id>1000000) { return new WP_Error('dr_id','Нужен положительный числовой ID таблицы.'); }
    $key='dr_widget_v3_' . md5($table_id . dr_site()['origin'] . $route);
    $cached=get_transient($key);
    if (!$refresh && is_array($cached)) {
        $registry=dr_widget_offers();
        foreach($cached['offers'] as &$offer) {
            $offer['verified']=!empty($registry[$offer['id']]['verified']);
            if(isset($registry[$offer['id']]['check'])) { $offer['check']=$registry[$offer['id']]['check']; }
        }
        unset($offer);
        // Cached HTML and the resolver registry must describe the same snapshot.
        dr_widget_register_offers($cached['offers'],$table_id,$route);
        return $cached;
    }
    $host=wp_parse_url(dr_site()['origin'],PHP_URL_HOST);
    $url=add_query_arg(array('hds_table_id'=>$table_id,'s'=>$host,'pg'=>$host.$route,'match_slug'=>'','cb'=>(string)round(microtime(true)*1000)),'https://cryptocasinokingdom.com/');
    $response=wp_safe_remote_get($url,array('user-agent'=>'Mozilla/5.0 (compatible; SiteTheme/1.0)','headers'=>array('Accept'=>'text/html,*/*;q=0.8'),'timeout'=>20,'redirection'=>0,'limit_response_size'=>3000000));
    if (is_wp_error($response)) { return new WP_Error('dr_provider','Не удалось получить таблицу от провайдера.'); }
    $status=wp_remote_retrieve_response_code($response);
    if ($status!==200) { return new WP_Error('dr_provider','Провайдер таблицы вернул HTTP '.$status.'.'); }
    $result=dr_widget_parse(wp_remote_retrieve_body($response),$table_id,$route);
    if (!is_wp_error($result)) { set_transient($key,$result,15*MINUTE_IN_SECONDS); }
    return $result;
}
function dr_widget_styles($table) {
    wp_enqueue_style('dr-hds-vendor','https://cryptocasinokingdom.com/_widget/2.0/style.css',array(),null);
    foreach(array('folders'=>'folders','offer_wall'=>'offer-wall') as $style=>$file) {
        if(strpos($table['html'],'data-style="'.$style.'"')!==false) {
            wp_enqueue_style('dr-hds-'.$file,'https://cryptocasinokingdom.com/_widget/2.0/'.$file.'.css',array('dr-hds-vendor'),null);
        }
    }
    wp_enqueue_style('dr-widget',get_template_directory_uri().'/assets/dr-widget.css',array('dr-hds-vendor'),'0.1.10');
}
function dr_casino_table_shortcode($attributes) {
    $attributes=shortcode_atts(array('id'=>'','row_height'=>''),$attributes,'casino_table');
    if (!preg_match('/^[1-9][0-9]{0,6}$/',(string)$attributes['id'])) { return '<p class="dr-widget-error">Укажите ID таблицы: [casino_table id="88"]</p>'; }
    $row_height=trim((string)$attributes['row_height']);
    if ($row_height!=='' && (!preg_match('/^[0-9]{2,3}$/',$row_height) || (int)$row_height<72 || (int)$row_height>240)) {
        return '<p class="dr-widget-error">Высота строки должна быть целым числом от 72 до 240: [casino_table id="88" row_height="110"]</p>';
    }
    $page=dr_page();
    if (!$page || !$page['casino']) { return '<p class="dr-widget-error">Таблица доступна на созданных казино-страницах.</p>'; }
    $id=(int)$attributes['id'];$table=dr_widget_data($id,$page['route']);
    if (is_wp_error($table)) {
        return '<div class="dr-widget-error" role="status">' . esc_html($table->get_error_message()) . '</div>';
    }
    dr_widget_styles($table);
    // No browser cookies or client targets participate in routing. The vendor
    // table is fetched once, sanitized and rewritten before entering the DOM.
    static $instance=0;$instance++;
    $layout=dr_casino_layout();
    $height_attributes=$row_height!=='' ? ' data-row-height="'.(int)$row_height.'"' : '';
    $layout_attributes=' data-dr-casino-layout="1" style="--dr-casino-table-width:'.($layout['table_width_px'] ? (int)$layout['table_width_px'].'px' : '100%').';--dr-offer-row-height:'.($row_height!=='' ? (int)$row_height : (int)$layout['row_height_px']).'px;--dr-casino-cell-padding:'.(int)$layout['cell_padding_px'].'px"';
    return '<div class="dr-casino-table" id="hdsTableWidget_'.$id.'_'.$instance.'" data-table-id="'.$id.'"'.$height_attributes.$layout_attributes.'>'.$table['html'].'</div>';
}
add_shortcode('casino_table','dr_casino_table_shortcode');
add_shortcode('hds_table','dr_casino_table_shortcode');
add_action('wp_enqueue_scripts',function() {
    $page=dr_page();$post=get_post();
    if($page && $page['casino'] && $post && (has_shortcode($post->post_content,'casino_table') || has_shortcode($post->post_content,'hds_table'))) {
        if(preg_match_all('/\[(?:casino_table|hds_table)\s+[^\]]*id=["\']?(\d+)/',$post->post_content,$ids)) {
            foreach($ids[1] as $id) { $table=dr_widget_data((int)$id,$page['route']);if(!is_wp_error($table)) { dr_widget_styles($table); } }
        }
    }
});

add_action('init',function() {
    $uri=isset($_SERVER['REQUEST_URI']) ? wp_unslash($_SERVER['REQUEST_URI']) : '';
    $path=strtok($uri,'?');
    if (strpos($path,'/go/')!==0) { return; }
    nocache_headers();header('X-DR-Go-Resolver: server-map-v2');
    if ($path==='/go/index.php' && isset($_GET['ping']) && $_GET['ping']==='1') { status_header(200);echo 'OK';exit; }
    $alias=rawurldecode(trim(substr($path,4),'/'));
    if (!preg_match('/^[a-z0-9]+(?:[.-][a-z0-9]+)*$/',$alias)) { status_header(404);echo 'Unknown offer';exit; }
    $referer=isset($_SERVER['HTTP_REFERER']) ? wp_unslash($_SERVER['HTTP_REFERER']) : '';
    $offer=dr_widget_resolve_offer(dr_widget_offers(),$alias,$referer);
    if (!$offer) { status_header(404);echo 'Unknown or ambiguous offer';exit; }
    if (!dr_widget_target_valid($offer['target'])) { status_header(404);echo 'Invalid offer';exit; }
    if ($path!=='/go/'.$alias || strpos($uri,'?')!==false) { wp_safe_redirect(home_url('/go/'.$alias),301,'Site redirect');exit; }
    // URL is a validated server-side value. Incoming query/cookie values are
    // never appended, decoded/re-encoded, or used as an external destination.
    wp_redirect($offer['target'],302,'Affiliate redirect');exit;
},-100);

add_action('admin_menu',function() {
    add_theme_page('Партнёрские ссылки','Партнёрские ссылки','manage_options','dr-affiliates','dr_affiliate_admin');
});
function dr_affiliate_admin() {
    if (!current_user_can('manage_options')) { return; }
    $offers=dr_widget_offers();
    if (isset($_POST['dr_offer_action'])) {
        check_admin_referer('dr_affiliates');
        $id=isset($_POST['offer']) ? sanitize_text_field(wp_unslash($_POST['offer'])) : '';
        if (isset($offers[$id])) {
            if ($_POST['dr_offer_action']==='check') {
                $url=$offers[$id]['target'];$chain=array();$final='';$status='unknown';
                for($hop=0;$hop<8;$hop++) {
                    if (!dr_widget_target_valid($url)) { $status='fail';break; }
                    $response=wp_safe_remote_get($url,array('user-agent'=>'Mozilla/5.0 (compatible; SiteTheme/1.0)','timeout'=>10,'redirection'=>0,'limit_response_size'=>100000));
                    if(is_wp_error($response)) { break; }
                    $code=wp_remote_retrieve_response_code($response);$location=wp_remote_retrieve_header($response,'location');
                    $chain[]=array('url'=>$url,'status'=>$code,'location'=>$location);
                    if(in_array($code,array(301,302,303,307,308),true) && $location) { $url=WP_Http::make_absolute_url($location,$url);continue; }
                    $status=$code===200 ? 'reachable' : 'unknown';$final=$url;break;
                }
                $offers[$id]['check']=array('status'=>$status,'chain'=>$chain,'final'=>$final,'at'=>time());$offers[$id]['verified']=false;
            } elseif ($_POST['dr_offer_action']==='confirm' && dr_widget_binding_valid($offers[$id]) && empty($offers[$id]['context_issues']) && isset($offers[$id]['check']['status']) && $offers[$id]['check']['status']==='reachable') {
                $offers[$id]['verified']=true;
            }
            update_option('dr_affiliate_offers',$offers,false);
        }
    }
    echo '<div class="wrap"><h1>Партнёрские ссылки</h1><p>Публичный адрес: /go/бренд, без параметров. Предложение выбирается на сервере с учётом страницы перехода. Проверьте цепочку, конечный бренд и партнёрские параметры. HTTP 200 сам по себе не подтверждает бренд. Неизвестные или неоднозначные /go/ возвращают 404. Входящие cookie и параметры назначения игнорируются.</p>';
    foreach($offers as $id=>$offer) {
        echo '<div style="background:white;padding:18px;margin:15px 0"><h2>'.esc_html($offer['brand'].' · /go/'.$offer['alias']).'</h2><p>Таблица '.(int)$offer['table_id'].' · '.esc_html($offer['route']).'</p><pre style="white-space:pre-wrap;overflow-wrap:anywhere">'.esc_html($offer['target']).'</pre>';
        if(!dr_widget_binding_valid($offer)) { echo '<p style="color:#b32d2e">Короткая ссылка не имеет однозначного актуального назначения для этой страницы. Обновите таблицу; для одного бренда на одной странице должно быть одно предложение.</p>'; }
        echo '<pre style="white-space:pre-wrap;overflow-wrap:anywhere">'.esc_html(wp_json_encode(array('parameters'=>$offer['parameters'],'context'=>$offer['context'],'errors'=>$offer['context_issues']),JSON_PRETTY_PRINT|JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)).'</pre>';
        if (isset($offer['check'])) { echo '<pre style="white-space:pre-wrap;overflow-wrap:anywhere">'.esc_html(wp_json_encode($offer['check'],JSON_PRETTY_PRINT|JSON_UNESCAPED_UNICODE|JSON_UNESCAPED_SLASHES)).'</pre>'; }
        echo '<p>'.(!empty($offer['verified']) ? 'Бренд и ссылка подтверждены владельцем.' : 'Бренд и ссылка ещё не подтверждены.').'</p><form method="post">';wp_nonce_field('dr_affiliates');
        echo '<input type="hidden" name="offer" value="'.esc_attr($id).'"><button class="button" name="dr_offer_action" value="check">Проверить переходы</button> <button class="button" name="dr_offer_action" value="confirm" '.(isset($offer['check']['status']) && $offer['check']['status']==='reachable' ? '' : 'disabled').'>Подтверждаю: конечный бренд и параметры верны</button></form></div>';
    }
    echo '</div>';
}
