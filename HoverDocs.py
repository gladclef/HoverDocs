import sublime
import sublime_plugin
import os
import html
import re

class HoverDocsCommand(sublime_plugin.TextCommand):
	def run(self, edit, mode="open", display_style=""):
		if mode == "append":
			self.view.insert(edit, self.view.size(), args['characters'])

class HoverDocsListener(sublime_plugin.EventListener):
	def __init__(self, *vargs, **kwargs):
		super().__init__(*vargs, **kwargs)
		self.hover_line = -1
		self.dclick_annotations = []
		self.pinned_annotations = []
		self.sel_snapshot = []
		self.double_click_target = None

	def setting(self, setting):
		return sublime.load_settings("HoverDocs.sublime-settings")[setting]

	def find_symbol_definition(self, view, sym_name):
		""" Searches the sublime index of symbols for the closest matching definition of sym_name.

		The "closest match" is according to the following rules, in order of presedence:
		1. the view's file
		2. open files
		3. files with the same extension as the given view
		4. "path distance" between the file for the given view and the definition file

		Args:
		    view: the active view
		    sym_name: the name of the symbol to look for
	    
	    Returns:
			The SymbolLocation, or None if not found.
			https://www.sublimetext.com/docs/api_reference.html#sublime.SymbolLocation
		"""
		win = sublime.active_window()
		sym_locs = win.symbol_locations(sym=sym_name)
		if len(sym_locs) > 0:
			_subl_definition_type = 1
			sym_loc = None
			fn = view.file_name()
			a, ext = os.path.splitext(fn)

			# find the best fitting definition
			defs = list(filter(lambda sl: sl.type == _subl_definition_type, sym_locs))
			if len(defs) == 0:
				return None

			# presedence (1)
			defs_samefile = list(filter(lambda sl: sl.path == fn, defs))
			if len(defs_samefile) > 0:
				defs = defs_samefile

			# For sorting by distances between two file paths. The distance for "foobar.txt" between the following example paths is 5:
			# /this/is/an/example/path/foobar.txt
			# /this/is/another/path/foobar.txt
			def get_dirs(path):
				ret = []
				path, base = os.path.split(path)
				while base != "":
					path, base = os.path.split(path)
					ret.append(base)
				ret.reverse()
				return ret
			fn_dirs = get_dirs(fn)
			def get_path_dist(sym_loc):
				sym_dirs = get_dirs(sym_loc.path)
				for i in range(len(fn_dirs)):
					if sym_dirs[i] != fn_dirs[i]:
						break
				path_dist = len(fn_dirs)-i + len(sym_dirs)-i
				return path_dist

			# Build a collection of filters to find the most appropriate result.
			# We filter down until there aren't any sym_locs left, or we've run out of filters.
			# The order of the filters matters.
			filter_open    = lambda locs: list(filter(lambda sl: win.find_open_file(sl.path) != None, locs)) # presedence (2)
			filter_extents = lambda locs: list(filter(lambda sl: sl.path.endswith(ext), locs))               # presedence (3)
			sort_path_dist = lambda locs: list(sorted(locs, key=get_path_dist))                              # presedence (4)
			filters = [filter_open, filter_extents, sort_path_dist]

			# Find the best fitting sym_loc
			sym_loc = defs[0]
			for sym_filter in filters:
				defs = sym_filter(defs)
				if len(defs) == 0:
					break
				sym_loc = defs[0]

			return sym_loc
		else:
			# print("No matching symbol at point")
			return None

	def on_text_command(self, view, command_name, args):
		if command_name == "hover_docs":
			if (args == None) or (not "mode" in args):
				args["mode"] = "open"
			if  args["mode"] == "open":
				regs = filter(lambda r: r.size() == 0, view.sel())
				doc_regs, doc_strs, sym_locs = [], [], []
				for reg in regs:
					doc_str, sym_loc, sym_reg = self.prep_doc(view, reg.a)
					if doc_str != None:
						doc_regs.append(sym_reg)
						doc_strs.append(doc_str)
						sym_locs.append(sym_loc)
				if len(doc_regs) > 0:
					fds = "" if (not "display_style" in args) else args["display_style"]
					self.add_docs(view, doc_regs, doc_strs, sym_locs, is_keybinding=True, force_display_style=fds)
				else:
					sublime.active_window().status_message("No definition found for symbol at cursor")
			elif args["mode"] == "clear":
				view.hide_popup()
				view.erase_regions("hd_hover")
		if command_name == "drag_select":
			for sel in view.sel():
				if sel not in self.sel_snapshot:
					self.double_click_target = sel
					break
			if "by" in args and args["by"] == "words":
				self.on_double_click(view, self.double_click_target)
			self.sel_snapshot = list(view.sel())

	def on_double_click(self, view, reg):
		view.hide_popup()
		if self.setting("show_on_double_click"):
			doc_str, sym_loc, sym_reg = self.prep_doc(view, reg.a)
			if doc_str != None:
				self.add_docs(view, [sym_reg], [doc_str], [sym_loc], is_double_click=True)

	def on_hover(self, view, point, hover_zone):
		if not self.setting("show_on_hover"):
			return

		doc_str, sym_loc, sym_reg = self.prep_doc(view, point)
		hover_line = view.rowcol(point)[0]

		if doc_str == None:
			if hover_line != self.hover_line:
				if self.setting("hover_auto_hide"):
					view.erase_regions("hd_hover")
		else:
			self.add_docs(view, [sym_reg], [doc_str], [sym_loc], is_hover=True)
		self.hover_line = hover_line

	def prep_doc(self, view, point, force_doc_string=None, force_interface=None, force_hyperlink=None, _look_behind=False):
		""" Finds the definition for the reference symbol at the given point (if any)
		and builds out the documentation string.

		Args:
		    view: the view to grab the symbol from
		    point: the location in the view to grab the symbol from
		    force*: True or False to force the documentation string, None to obey the settings file
		    _look_behind: Private. Look for a symbol at point-1, in case we're at the end of a word.
		Returns:
		    doc_str: The documentation string, or None if not applicable
		    sym_loc: The SymbolLocation, or None if not applicable
		    sym_reg: The symbol region in the given view.
		"""
		if _look_behind:
			point -= 1
		if point < 0 or point > view.size():
			return None, None, None
		point_reg = sublime.Region(point, point)
		scope = view.extract_tokens_with_scopes(point_reg)
		regs = []
		annotations = []
		sym_loc = None

		# some basic qualifications
		if len(scope) == 0:
			if not _look_behind:
				return self.prep_doc(view, point, force_doc_string, force_interface, force_hyperlink, _look_behind=True)
			else:
				return None, None, None
		sym_reg = scope[0][0]
		scope_names = scope[0][1]
		if "comment" in scope_names:
			return None, None, None

		# check if this symbol _is_ the definition
		view_sym_regs = view.symbol_regions()
		found = False
		for view_sym_reg in view_sym_regs:
			if view_sym_reg.region.contains(point):
				if view_sym_reg.type == 1: # 1 == Definition
					return None, None, None

		# symbol at sym_reg must be a reference, try to find the definition
		# try to find a definition with the same name in the index
		sym_name = view.substr(sym_reg)
		sym_loc = self.find_symbol_definition(view, sym_name)
		if sym_loc == None:
			if not _look_behind:
				return self.prep_doc(view, point, force_doc_string, force_interface, force_hyperlink, _look_behind=True)
			else:
				return None, None, None
		fn = os.path.basename(sym_loc.path)

		# get the def_str and comment_str, with syntax applied via minihtml
		v2, def_reg, comment_reg = self.find_def_and_comment(sym_loc, sym_name)
		if v2 == 0:
			return None, None, None
		def_scopes, comment_scopes = self.get_scope_spans(v2, def_reg), self.get_scope_spans(v2, comment_reg)
		def_str, comment_str = v2.substr(def_reg), v2.substr(comment_reg)
		def_str = self.apply_syntax(v2, def_str, def_scopes)
		comment_str = self.apply_syntax(v2, comment_str, comment_scopes)
		
		# build the doc_str
		doc_str = ""
		if (self.setting("display_docstring") or force_doc_string == True) and (force_doc_string != False):
			if len(def_str) > 0:
				doc_str += def_str
		if (self.setting("display_interface") or force_interface == True) and (force_interface != False):
			if len(comment_str) > 0:
				doc_str += ("" if len(doc_str) == 0 else "<br>") + comment_str
		if (self.setting("display_file_hyperlink") or force_hyperlink == True) and (force_hyperlink != False):
			doc_str += ("" if len(doc_str) == 0 else "<br>") + f"<a href='goto:!href!'>{fn}:{sym_loc.row+1}</a>"

		return doc_str, sym_loc, sym_reg

	def add_docs(self, view, doc_regs, doc_strs, sym_locs, is_hover=False, is_double_click=False, is_keybinding=False, force_display_style=""):
		# add close buttons
		auto_hide = True
		if (is_hover and not self.setting("hover_auto_hide")) or \
		   (is_double_click and not self.setting("double_click_auto_hide")) or \
		   (is_keybinding and not self.setting("keybinding_auto_hide")):
			auto_hide = False
		if not auto_hide:
			close_button = f" <a href='close:!href!'>close</a>"
			doc_strs = list(map(lambda s: s+close_button, doc_strs))

		# add the link index to the href
		for i in range(len(doc_strs)):
			doc_strs[i] = doc_strs[i].replace("!href!", str(i))

		# determine the display style
		is_ctrl = self.is_ctrl_pressed() and self.setting("toggle_display_style") and not is_keybinding
		display_style = "annotation" if (self.setting("display_style") == "annotation") else "popup"
		if is_ctrl:
			display_style = "annotation" if (display_style == "popup") else "popup"
		if force_display_style != "":
			display_style = force_display_style

		if display_style == "annotation":
			flags = 128 # RegionFlags.HIDDEN
			view.add_regions(key="hd_hover", regions=doc_regs, scope='', icon='', flags=flags, annotations=doc_strs,
				             annotation_color='', on_navigate=lambda href: self.on_navigate(href, view, sym_locs))
		else: # "popup"
			flags = 2+16 # COOPERATE_WITH_AUTO_COMPLETE, KEEP_ON_SELECTION_MODIFIED
			if auto_hide:
				flags += 8 # HIDE_ON_MOUSE_MOVE_AWAY
			view_width, view_height = view.viewport_extent()
			view.show_popup(doc_strs[0], flags, doc_regs[0].a, max_width=view_width, max_height=view_height,
			                on_navigate=lambda href: self.on_navigate(href, view, sym_locs))

	def apply_syntax(self, view, strval, scope_spans):
		""" Inserts minihtml into the given string to match the syntax of the given spans.

		Args:
		    view: The view that the given string is from.
		    strval: The string to insert the syntax into.
		    scope_spans: A list of [idx, len, scope_name] used to get the syntax.
		Returns:
		    str_wsyntax: The string with html markup inserted.
		"""
		ret = ""

		# get the default foreground color
		default_style = view.style_for_scope('')

		for scope_span in scope_spans:
			# split the string into scope span pieces
			idx, length, scope_names = scope_span
			strpart = strval[idx:idx+length]

			# html encode the string
			strpart = html.escape(strpart)
			strpart = re.sub(r" ( +)", lambda m: "&nbsp;"*len(m.group(0)), strpart)
			strpart = strpart.replace("\n","<br>")

			# get the style for this scope
			style = view.style_for_scope(scope_names[0])
			for scope_name in scope_names[1:]:
				tmp_style = view.style_for_scope(scope_name)
				if style['foreground'] == default_style['foreground']:
					style = tmp_style
				if tmp_style['foreground'] != default_style['foreground']:
					style = tmp_style

			# apply the syntax for this piece
			style_str = f"<div style='display:inline; color:{style['foreground']};"
			if "background" in style:
				style_str += " background-color:{style['background']};"
			if "bold" in style and style["bold"]:
				style_str += " font-weight:bold;"
			if "italic" in style and style["italic"]:
				style_str += " font-style:italic;"
			if "underline" in style and style["underline"]:
				style_str += " text-decoration:underline;"
			ret += f"{style_str}'>{strpart}</div>"

		return ret

	def find_def_and_comment(self, sym_loc, sym_name):
		""" For a given symbol, get the definition string and the comment string.

		Args:
		    sym_loc: The SymbolLocation for the symbol. Probably from find_symbol_definition(...)
		    sym_name: The string representing the name of the symbol.
		Returns:
		    v2: The temporary output panel into which the symbol is loaded.
		    def_reg: The region containing the definition of the symbol. If a function, then
		             this includes the parameters in the definition. Might be empty.
		    comment_reg: The region containing the comment immediately preceeding or following
		                 the symbol. Empty region if not found.
		"""
		# find the view for the given sym_loc, if already opened somewhere
		windows = [sublime.active_window()] + sublime.windows()
		for window in windows:
			v2 = window.find_open_file(sym_loc.path)
			if v2 != None:
				break

		# load in a new view for this unopened file
		if v2 == None:
			# find the syntax
			syntax = sublime.find_syntax_for_file(sym_loc.path)
			if syntax == None:
				return None, sublime.Region(0,0), sublime.Region(0,0)

			# create a hidden output panel with the surrounding 10 lines around the symbol
			sublime.active_window().destroy_output_panel("hd_output_panel")
			v2 = sublime.active_window().create_output_panel("hd_output_panel", True)
			start = max(0, sym_loc.row - 10)
			stop = sym_loc.row + 10
			pre_line, sym_line, post_line, sym_reg = 0, 0, None
			with open(sym_loc.path, 'r') as f:
				lineno = 0
				for line in f:
					lineno += 1
					if lineno >= start:
						if lineno == sym_loc.row:
							sym_line = v2.size()
							sym_reg = sublime.Region(v2.size()+sym_loc.col-1, v2.size()+sym_loc.col+len(sym_name)-1)
						elif lineno < sym_loc.row:
							pre_line = v2.size()
						v2.run_command("hover_docs", args={ "mode": "append", "args": { "characters": line } })
						if lineno == sym_loc.row:
							post_line = v2.size()
					if lineno >= stop:
						break
				stop = lineno
			pre_line = v2.line(pre_line)
			sym_line = v2.line(sym_line)
			post_line = v2.line(post_line)

			# set the syntax and let sublime parse the hidden output panel
			v2.assign_syntax(syntax)
		else:
			pos = self.get_pos(v2, sym_loc.row, sym_loc.col)
			sym_reg = sublime.Region(pos, pos+len(sym_name))
			pre_line = v2.line(v2.line(pos).a-1)
			sym_line = v2.line(pos)
			post_line = v2.line(v2.line(pos).b+1)

		# get the defstr from the symbol line
		# expand to the end of the function parameters
		sym_scopes = v2.scope_name(sym_reg.b).split(" ")
		sym_scopes = filter(lambda s: "parameters" in s, sym_scopes)
		sym_scopes = map(lambda s: s[:s.index("parameters")+10], sym_scopes)
		tmp_reg = self.expand_to_scope(v2, sym_reg.b, sym_scopes)
		sym_extracted_reg = sublime.Region(sym_reg.a, max(sym_reg.b, tmp_reg.b))
		def_reg = sym_extracted_reg

		# get the comment line(s), either on or above or below the symbol line
		comment_reg = None
		for line in [sym_line, pre_line, post_line]:
			scope_spans = self.get_scope_spans(v2, line)
			for ss in scope_spans:
				# is this scope span part of a comment
				found = False
				for scope_name in ss[2]:
					if "comment" in scope_name:
						found = True
						break
				if not found:
					continue

				# expand the scope span
				for pnt in [line.a+ss[0], line.a+ss[0]+ss[1]-1]:
					reg = v2.extract_scope(pnt)
					if reg != None:
						if comment_reg == None:
							comment_reg = reg
						else:
							comment_reg = sublime.Region(min(comment_reg.a, reg.a), max(comment_reg.b, reg.b))
			if comment_reg != None:
				break
		if comment_reg == None:
			comment_reg = sublime.Region(0,0)
		
		return v2, def_reg, comment_reg

	def expand_to_scope(self, view, point, matching_scopes):
		""" Finds the extent of the region that matches the given scopes.

		Args:
		    view: The view to search in
		    point: Where to start the region
		    matching_scopes: A list of strings that the scope names should start with
		Returns:
		    reg: The expanded region
		"""
		if type(matching_scopes) != list:
			matching_scopes = list(matching_scopes)
		beggining, ending = point, point

		# find the extent of the matching scopes
		for start, mod in [(point-1, -1), (point, 1)]:
			pos = start
			while pos > 0 and pos < view.size():
				scope_names = view.scope_name(pos).split(" ")
				scope_names = filter(lambda s: s != "", scope_names)
				found = False
				for scope_name in scope_names:
					for matching_scope in matching_scopes:
						if scope_name.startswith(matching_scope):
							found = True
							break
					if found:
						break
				if not found:
					break

				pos += mod

			if mod == -1:
				beggining = pos
			else:
				ending = pos

		reg = sublime.Region(beggining, ending)
		return reg

	def get_scope_spans(self, view, reg):
		""" Get the scope names for each character in a region.

		Args:
		    view: The containing view of the given region.
		    reg: The region to look in.
		Returns:
		    scope_spans: A list of [idx, len, scope_names].
		"""
		scope_spans = []

		last = []
		start = 0
		cnt = 1
		for i in range(reg.a, reg.a+reg.size()+1):
			scope_names = view.scope_name(i).split(" ")
			scope_names = list(filter(lambda s: s != "", scope_names))
			if i == reg.a:
				last = scope_names
				cnt = 1
			elif last == scope_names:
				cnt += 1
			else: # last != scope_names
				scope_spans.append([start, cnt, last])
				start = i - reg.a
				last = scope_names
				cnt = 1
		scope_spans.append([start, cnt, last])

		return scope_spans

	def get_pos(self, view, row, col):
		lines = view.lines(sublime.Region(0, view.size()))
		# print(f"lines[0-{len(lines)-1}], row: {row}, size: {view.size()}")
		line = lines[row-1]
		pos = line.a+col-1
		return pos

	def move_to(self, view, row, col):
		pos = self.get_pos(view, row, col)

		view.window().focus_view(view)
		view.sel().clear()
		view.sel().add(pos)
		view.show_at_center(pos)

	def is_ctrl_pressed(self):
		# from uiautomation
		# https://github.com/yinkaisheng/Python-UIAutomation-for-Windows/blob/master/uiautomation/uiautomation.py
		try:
			import ctypes
			state = ctypes.windll.user32.GetAsyncKeyState(0x11)
			return bool(state & 0x8000)
		except Exception as e:
			pass
		return False

	def on_navigate(self, href, view, sym_locs):
		# parse the href
		parts = href.split(':')
		action = parts[0]
		index = int(parts[1])
		sym_loc = sym_locs[index]

		# find the view
		v2 = None
		for win in sublime.windows():
			v2 = win.find_open_file(sym_loc.path)
			if v2 != None:
				break
		if v2 == None:
			sublime.active_window().status_message(f"Can't find view for file {sym_loc.path}")
			return

		if action == "close":
			v2.erase_regions("hd_hover")
			v2.hide_popup()
		else: # "goto"
			open_as_transient = self.setting("open_hyperlink_as_transient")
			if self.is_ctrl_pressed():
				open_as_transient = not open_as_transient

			if not open_as_transient:
				self.move_to(v2, sym_loc.row, sym_loc.col)
			else:
				flags = 1+16+32 # encoded position, semi-transient, add to selection
				v2 = sublime.active_window().open_file(f"{sym_loc.path}:{sym_loc.row}:{sym_loc.col}", flags=flags)
