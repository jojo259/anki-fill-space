from aqt import gui_hooks, mw
import json

# The JavaScript to inject
JS_CODE = """
<script>
(function() {
    // Idempotency check: prevent running multiple times if injected twice
    if (window.__FillSpaceInited) return;
    window.__FillSpaceInited = true;

    const TIMEOUT_MS = 0;

    let resizeTimer;
    let observerTimer;
    let lastLogMsg = "";
    
    function createLogBox() {
        if (!window.FillSpaceConfig || !window.FillSpaceConfig.showLog) return;
        if (document.getElementById('fill-space-log')) return;
        if (!document.body) return;
        
        const logBox = document.createElement('div');
        logBox.id = 'fill-space-log';
        logBox.style.cssText = "position:fixed; bottom:5px; right:5px; background:rgba(0,0,0,0.8); color:#0f0; font-family:monospace; font-size:12px; padding:6px; z-index:99999; pointer-events:none; border-radius:3px; max-width:300px; white-space:pre-wrap; border: 1px solid #0f0;";
        logBox.innerText = "Fill-Space: Ready";
        document.body.appendChild(logBox);
    }

    function log(msg, forceConsole = false) {
        if (window.FillSpaceConfig && window.FillSpaceConfig.showLog) {
            createLogBox();
            const box = document.getElementById('fill-space-log');
            if (box) {
                 // Avoid DOM thrashing if message is same
                 if (box.innerText !== "Fill-Space: " + msg) {
                     box.innerText = "Fill-Space: " + msg;
                 }
            }
        }
        
        // Only log to console if it changed or forced, to avoid spam
        if (forceConsole || lastLogMsg !== msg) {
             console.log("[Fill-Space] " + msg);
             lastLogMsg = msg;
        }
    }

    function fitImage() {
        // 1. Check Python-detected Note Type
        if (window.FillSpaceConfig && window.FillSpaceConfig.isImageOcclusion) {
             log("Image Occlusion detected (Note Type). Skipping.");
             return;
        }

        // 2. Fallback DOM checks
        if (document.getElementById('io-wrapper') || 
            document.getElementById('io-overlay') || 
            document.getElementById('io-header') ||
            document.querySelector('.io-wrapper')) {
             log("Image Occlusion detected (DOM). Skipping.");
             return;
        }

        const images = Array.from(document.getElementsByTagName('img'));
        
        const candidates = images.filter(img => {
            if (img.offsetParent === null) return false;
            const style = window.getComputedStyle(img);
            if (style.display === 'none' || style.visibility === 'hidden') return false;
            if (img.complete && (img.naturalWidth < 16 || img.naturalHeight < 16)) return false;
            // Removed SVG check since flags are often SVGs
            return true;
        });

        if (candidates.length !== 1) {
            if (candidates.length > 0) {
                 log(`Inactive. Candidates: ${candidates.length}/${images.length}`);
            }
            return;
        }

        const img = candidates[0];
        
        // --- 1. PREPARE MEASUREMENT ---
        const originalDisplay = img.style.display;
        img.style.display = 'none';

        // We must ensure the container collapses to fit just the text.
        // We walk up from the image to the body and reset heights on ALL ancestors.
        // This defeats layouts that use 'height: 100%' or 'flex: 1' on wrappers.
        const overrides = [];
        
        function override(el) {
            if (!el || !el.style) return;
            overrides.push({
                el: el,
                cssText: el.style.cssText,
                priorityHeight: el.style.getPropertyPriority('height'),
                priorityMinHeight: el.style.getPropertyPriority('min-height'),
                priorityFlex: el.style.getPropertyPriority('flex'),
                priorityBasis: el.style.getPropertyPriority('flex-basis')
            });
            // Force collapse
            el.style.setProperty('height', 'auto', 'important');
            el.style.setProperty('min-height', '0px', 'important');
            el.style.setProperty('flex', '0 1 auto', 'important');
            el.style.setProperty('flex-basis', 'auto', 'important');
        }

        // 1. Ancestors of image
        let parent = img.parentElement;
        while (parent && parent !== document.documentElement) {
            override(parent);
            parent = parent.parentElement;
        }
        
        // 2. The document roots
        override(document.body);
        override(document.documentElement);

        // --- 2. MEASURE ---
        // With everything collapsed, the body height should represent exactly the content height
        // contentHeight is either body scrollHeight or the .card height if available
        let contentHeight = document.body.scrollHeight;
        
        // If there's a specific .card container, its height usually represents the "paper"
        const card = document.querySelector('.card');
        if (card) {
            contentHeight = card.scrollHeight;
        }

        const viewportHeight = window.innerHeight;

        // --- 3. RESTORE ---
        overrides.forEach(o => {
            o.el.style.cssText = o.cssText; 
            // cssText restore might be enough, but sometimes inline styles are tricky.
            // The previous 'setProperty' approach is safer if we want to respect existing priorities?
            // Actually cssText overwrites everything we did, restoring exact state.
            // But we must be careful not to nuke styles applied by other things in that split second? 
            // Since JS is single threaded, this is safe.
        });
        img.style.display = originalDisplay;

        // --- 4. CALCULATE ---
        const style = window.getComputedStyle(img);
        const marginTop = parseFloat(style.marginTop) || 0;
        const marginBottom = parseFloat(style.marginBottom) || 0;
        
        // If the card has padding, we might have double-counted it in contentHeight 
        // vs viewport, but usually viewportHeight - contentHeight works well.
        
        // NOTE: if the card is "centered" vertically (margin: auto), this logic handles it 
        // because we collapsed the margins during measurement (height: auto).
        
        let available = viewportHeight - contentHeight - marginTop - marginBottom - 10;
        
        // Box sizing adjustments
        if (style.boxSizing === 'content-box') {
            const borderTop = parseFloat(style.borderTopWidth) || 0;
            const borderBottom = parseFloat(style.borderBottomWidth) || 0;
            const paddingTop = parseFloat(style.paddingTop) || 0;
            const paddingBottom = parseFloat(style.paddingBottom) || 0;
            available -= (borderTop + borderBottom + paddingTop + paddingBottom);
        }

        // Reduce by 5% buffer
        available = available * 0.95;

        if (available < 50) available = 50; 
        
        // IO Safe Check: Double check we aren't handling an IO image that wasn't caught by Note Type
        // If the image is inside an SVG, ignore it? (Usually not the case)
        
        // --- 5. APPLY ---
        // We set the container up to allow the image to be as big as possible within the constraints.
        const newMaxHeight = available + 'px';
        
        // We must apply important to override any existing CSS.
        // We set height to the calculated available space to force it to try and fill it, 
        // but max-height constrains it.
        // Actually, better pattern for "maximise but preserve aspect ratio":
        // max-height: [available]px
        // height: auto (or 100% if we want to force stretch but object-fit handles it)
        // width: auto
        // max-width: 100%
        // object-fit: contain
        
        // If we want it to literally fill the space:
        if (img.style.maxHeight !== newMaxHeight) {
             img.style.setProperty('max-height', newMaxHeight, 'important');
        }
        
        img.style.setProperty('max-width', '100%', 'important');
        img.style.setProperty('width', 'auto', 'important');
        
        // Key change: If we just set max-height, a small image might stay small.
        // We want to force it to expand.
        // width: auto, height: [available]px + object-fit: contain ?
        // or height: auto?
        
        // If we set height: available px, then width will scale. 
        // If width exceeds screen, max-width kicks in, and object-fit ensures it's visible.
        // unique approach:
        
        img.style.setProperty('height', newMaxHeight, 'important');
        img.style.setProperty('object-fit', 'contain', 'important');
        
        if (style.display === 'inline' || style.display === 'inline-block') {
             img.style.setProperty('display', 'block', 'important');
        }
        img.style.setProperty('margin-left', 'auto', 'important');
        img.style.setProperty('margin-right', 'auto', 'important');
        
        log(`Active. View(${viewportHeight}) - Content(${contentHeight}) = ${Math.round(available)}px`);
    }

    function onResize() {
        clearTimeout(resizeTimer);
        resizeTimer = setTimeout(fitImage, TIMEOUT_MS);
    }

    function init() {
        try {
            fitImage();
            
            const observer = new MutationObserver((mutations) => {
                 // Check if mutations are relevant (i.e. not our log box)
                 let relevant = false;
                 for (let m of mutations) {
                     if (m.target.id === 'fill-space-log' || m.target.parentNode && m.target.parentNode.id === 'fill-space-log') continue;
                     relevant = true; 
                     break;
                 }
                 if (!relevant) return;

                 // Debounce observer
                 clearTimeout(observerTimer);
                 observerTimer = setTimeout(fitImage, TIMEOUT_MS);
            });
            if (document.body) {
                 observer.observe(document.body, {childList: true, subtree: true, attributes: true, characterData: true});
            }
            
            document.querySelectorAll('img').forEach(img => {
                if (!img.complete) {
                    img.addEventListener('load', fitImage);
                }
            });
            
        } catch (e) {
            console.error("[Fill-Space] Error in init:", e);
        }
    }

    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', init);
    } else {
        init();
    }

    window.addEventListener('resize', onResize);
    window.addEventListener('load', fitImage);

})();
</script>
"""

def on_card_will_show(text: str, card, kind):
    config = mw.addonManager.getConfig(__name__) or {}
    show_log = json.dumps(config.get('show_log_on_screen', False))
    
    # Check for Image Occlusion in note type name
    # We use a try/except block to be safe, though card.note() should always exist
    try:
        note_type = card.note().model()['name'].lower()
        is_io = "image occlusion" in note_type
    except:
        is_io = False

    config_script = f"""
<script>
window.FillSpaceConfig = {{
    showLog: {show_log},
    isImageOcclusion: {json.dumps(is_io)}
}};
</script>
"""
    return text + config_script + JS_CODE

gui_hooks.card_will_show.append(on_card_will_show)
