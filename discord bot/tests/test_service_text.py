from app.service_text import format_service_description

SAMPLE = '''<ul class="sOrderingHighlights__shortlist">
<li><b>Starting Time:</b> 0-12 hours</li>
<li><b>Speed:</b> 200-400 followers per day</li>
<li><b>Guarantee:</b> 30 Days manual refill (you can make refill by pressing "refill button" in "order history" page)</li>
<li><b>Quality of accounts:</b> Medium</li>
<li><b>Targeting:</b> WorldWide</li>
</ul> <p><b>Correct Link Format:</b> Username or full URL</p>'''

def test_html_to_readable_list():
    text = format_service_description(SAMPLE)
    assert '<li>' not in text
    assert '• Starting Time: 0-12 hours' in text
    assert '• Speed: 200-400 followers per day' in text
    assert '• Guarantee: 30 Days manual refill' in text
    assert 'Correct Link Format: Username or full URL' in text

def test_nested_tags_entities_and_breaks():
    text = format_service_description('<div>Likes &amp; views<br><strong>Fast</strong></div>')
    assert 'Likes & views\nFast' in text

def test_unsafe_tags_are_not_shown():
    text = format_service_description('<p>Hello</p><script>hack()</script><p>Safe</p>')
    assert 'hack()' not in text and 'Safe' in text

def test_neutralizes_mass_mentions_and_truncates():
    text = format_service_description('<p>@everyone @here</p>'+'x'*2500,limit=180)
    assert '@everyone' not in text and len(text)<=180

def test_plain_text_and_empty():
    assert format_service_description('Normal description') == 'Normal description'
    assert 'No additional description' in format_service_description(None)
