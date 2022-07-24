# HoverDocs
In-line documentation display in Sublime Text.

## Features
* finds definitions and documentation
* displays as a popup (below text) or annotation (to the right side)
* hyperlink to quickly move to the definition, or open it in a transient view
* configure popups to show with hover, double click, or key binding
* toggle how hyperlinks work with ctrl key (Windows only)

## Demo
![Demo Gif](https://github.com/gladclef/HoverDocs/blob/master/demo.gif)

## Is this package right for me?
If you haven't heard of the LSP package before then [please go look at it!](https://packagecontrol.io/packages/LSP)
It is a very cool plugin that talks to language servers for your language
of choice to get more intelligent information about your code.

That being said, there are certain situations in which this package is more
useful. HoverDocs is lightweight, doesn't require the installation of
additional language servers, and doesn't require any complex environment
setup. If any of these situations sound like they apply to you, then you've
come to the right place.

## Installation
You can install the package with Package Control or manually.

### Install with Package Control
1. Press Ctrl+Shift+P (or Cmd+Shift+P on Mac) to open the Command Palette
2. Select Package Control: Install Package
3. Select HoverDocs

### Install Manually
1. Click the Preferences > Browse Packages… menu, this should open a folder Packages.
2. Download https://github.com/gladclef/HoverDocs and move it to that directory.
3. Restart Sublime Text.

## Key Bindings
There's some example key bindings available. Go to
Preferences > Package Settings > HoverDocs > Key Bindings
to set them.

## Caveats
I've only tested this package with python code. It should be compatible
with any language. If you try it and have any issues, please
[let me know!](https://github.com/gladclef/HoverDocs/issues)