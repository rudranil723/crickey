from html.parser import HTMLParser

class MyHTMLParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.in_card = False
        self.card_depth = 0
        self.tags = []

    def handle_starttag(self, tag, attrs):
        attr_dict = dict(attrs)
        classes = attr_dict.get('class', '')
        if 'match-card-wrapper' in classes:
            self.in_card = True
            self.card_depth = 1
            self.tags.append(f'<{tag} class="{classes}">')
        elif self.in_card:
            self.card_depth += 1
            self.tags.append(f'<{tag} class="{classes}">')

    def handle_endtag(self, tag):
        if self.in_card:
            self.card_depth -= 1
            if self.card_depth == 0:
                self.in_card = False
                self.tags.append(f'</{tag}> (End Card)')

    def handle_data(self, data):
        if self.in_card:
            text = data.strip()
            if text:
                self.tags.append(text)

parser = MyHTMLParser()
with open('debug_match_list.html', encoding='utf-8') as f:
    parser.feed(f.read())

for t in parser.tags[:100]:
    print(t)
