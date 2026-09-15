<?php
// Contract checks with WordPress hooks stubbed. Real WP/theme validation is a deployment gate.
define('ABSPATH', '/fixture/');
$hooks=[]; $routes=[]; $meta=[]; $options=[];
function add_action($name,$callback,$priority=10) { global $hooks; $hooks[$name][]=$callback; }
function add_filter($name,$callback,$priority=10) { }
function current_user_can($capability,$id=null) { return true; }
function register_post_meta($type,$key,$args) { global $meta; $meta[$type][$key]=$args; }
function register_rest_route($namespace,$route,$args) { global $routes; $routes[$route]=$args; }
function update_option($key,$value,$autoload=false) { global $options; $options[$key]=$value; }
function wp_json_encode($value,$flags=0) { return json_encode($value,$flags); }
class WP_Error { public function __construct(...$args) {} }
function check($condition,$message) { if (!$condition) { throw new RuntimeException($message); } }
require __DIR__.'/../integrations/wordpress/denzo-seo/denzo-seo.php';
foreach ($hooks['init'] as $hook) { $hook(); }
foreach ($hooks['rest_api_init'] as $hook) { $hook(); }
check(isset($meta['post']['denzo_revision']) && isset($meta['page']['denzo_schema']), 'Missing REST metadata');
check($routes['/capabilities']['callback']()['seo_metadata']===true, 'Missing capability');
$request = new class {
    public function get_param($name) { return ['indexnow_key'=>str_repeat('a',32),'llms_text'=>'# Actual studio'][$name] ?? null; }
};
check($routes['/resources']['callback']($request)['ok']===true, 'Resource request failed');
check($options['denzo_llms_text']==='# Actual studio', 'llms_text not persisted');
check($options['denzo_indexnow_key']===str_repeat('a',32), 'Wrong key');
check(strpos(denzo_sanitize_schema('{"description":"</script><script>alert(1)</script>"}'),'</script>')===false, 'Unsafe structured data');
echo "WordPress connector contracts passed\n";
