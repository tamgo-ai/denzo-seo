<?php
/**
 * Plugin Name: DENZO SEO Connector
 * Description: Authenticated SEO publishing, revision verification and discovery resources for DENZO.
 * Version: 1.0.0
 * Requires PHP: 7.4
 */
if (!defined('ABSPATH')) { exit; }

add_action('init', function () {
    foreach (array('post', 'page') as $type) {
        foreach (array('title','description','schema','revision','tenant') as $field) {
            register_post_meta($type, 'denzo_' . $field, array(
                'type' => 'string', 'single' => true,
                'show_in_rest' => array('schema' => array('type'=>'string', 'context'=>array('edit'))),
                'auth_callback' => function ($allowed, $key, $post_id) { return current_user_can('edit_post', $post_id); },
                'sanitize_callback' => $field === 'schema' ? 'denzo_sanitize_schema' : 'sanitize_text_field',
            ));
        }
    }
});

function denzo_sanitize_schema($value) {
    $decoded = json_decode($value, true);
    return is_array($decoded) ? wp_json_encode($decoded, JSON_HEX_TAG | JSON_HEX_AMP) : '';
}

add_action('rest_api_init', function () {
    register_rest_route('denzo-seo/v1','/capabilities',array(
        'methods'=>'GET',
        'permission_callback'=>function () { return current_user_can('edit_posts') || current_user_can('edit_pages'); },
        'callback'=>function () { return array('version'=>'1.0.0','seo_metadata'=>true,
            'publish_pages'=>current_user_can('publish_pages'),'publish_posts'=>current_user_can('publish_posts'),
            'manage_resources'=>current_user_can('manage_options')); },
    ));
    register_rest_route('denzo-seo/v1','/resources',array(
        'methods'=>'POST','permission_callback'=>function () { return current_user_can('manage_options'); },
        'callback'=>function ($request) {
            $key=$request->get_param('indexnow_key');
            $text=$request->get_param('llms_text');
            if ($key !== null) {
                if (!is_string($key) || !preg_match('/^[a-f0-9]{32,64}$/D',$key)) {
                    return new WP_Error('invalid_key','Invalid IndexNow key',array('status'=>400));
                }
                update_option('denzo_indexnow_key',$key,false);
            }
            if ($text !== null) {
                if (!is_string($text) || strlen($text)>100000) {
                    return new WP_Error('invalid_text','Resource is too large',array('status'=>400));
                }
                update_option('denzo_llms_text',$text,false);
            }
            return array('ok'=>true);
        },
    ));
});

function denzo_current_meta($key) {
    return is_singular() ? get_post_meta(get_queried_object_id(),'denzo_'.$key,true) : '';
}

foreach (array('pre_get_document_title','wpseo_title','rank_math/frontend/title') as $filter) {
    add_filter($filter,function ($old) { return denzo_current_meta('title') ?: $old; },99);
}
foreach (array('wpseo_metadesc','rank_math/frontend/description') as $filter) {
    add_filter($filter,function ($old) { return denzo_current_meta('description') ?: $old; },99);
}

add_action('wp_head',function () {
    $revision=denzo_current_meta('revision');
    if (!$revision) { return; }
    echo '<meta name="denzo-revision" content="'.esc_attr($revision).'">' . "\n";
    if (!defined('WPSEO_VERSION') && !defined('RANK_MATH_VERSION')) {
        echo '<meta name="description" content="'.esc_attr(denzo_current_meta('description')).'">' . "\n";
        echo '<meta property="og:title" content="'.esc_attr(denzo_current_meta('title')).'">' . "\n";
        echo '<meta property="og:description" content="'.esc_attr(denzo_current_meta('description')).'">' . "\n";
        echo '<meta property="og:url" content="'.esc_url(get_permalink()).'">' . "\n";
        $content=get_post_field('post_content',get_queried_object_id());
        if (preg_match('/<img[^>]+src=["\x27]([^"\x27]+)["\x27]/i',$content,$match)) {
            echo '<meta property="og:image" content="'.esc_url($match[1]).'">' . "\n";
        }
    }
    $schema=denzo_sanitize_schema(denzo_current_meta('schema'));
    if ($schema) { echo '<script type="application/ld+json">'.$schema.'</script>' . "\n"; }
},20);

// Only DENZO content is styled; theme navigation and existing client pages are preserved.
add_action('wp_enqueue_scripts',function () {
    if (!denzo_current_meta('revision')) { return; }
    wp_register_style('denzo-content',false,array(),'1.0.0');
    wp_enqueue_style('denzo-content');
    wp_add_inline_style('denzo-content','.denzo-content{max-width:74rem;margin:auto;line-height:1.65}.denzo-content img{max-width:100%;height:auto}.denzo-content .services-grid,.denzo-content .process-steps,.denzo-content .stats-bar{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:1.25rem}.denzo-content .service-card,.denzo-content .step{padding:1rem;border:1px solid #ddd;border-radius:.6rem}.denzo-content h2{margin-top:2rem}.denzo-content .btn-primary{display:inline-block;padding:.8rem 1.3rem;border-radius:.4rem;background:#123b52;color:white}.denzo-content .hero-section{padding:2rem 0}');
});

// WordPress's front controller serves these paths as plain text, never as HTML pages.
add_action('parse_request',function ($wp) {
    $path=trim($wp->request,'/');
    $key=get_option('denzo_indexnow_key','');
    $body=null;
    if ($key && $path === $key.'.txt') { $body=$key; }
    if ($path === 'llms.txt') { $body=get_option('denzo_llms_text',null); }
    if ($body !== null) {
        status_header(200); header('Content-Type: text/plain; charset=utf-8');
        header('X-Content-Type-Options: nosniff'); echo $body; exit;
    }
});
