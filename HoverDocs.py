import sublime
import sublime_plugin
import os
import html
import re
import copy

class HoverDocsCommand(sublime_plugin.TextCommand):
	""" Mostly here so that I can trick sublime into thinking there's a
	hover_docs command, which then gets interpretted by the
	HoverDocsListener.on_text_command(...).
	"""
	def run(self, edit, mode="open", display_style="", characters="", reg_str=""):
		if mode == "append":
			self.view.insert(edit, self.view.size(), characters)
		if mode == "replace":
			reg_parts = reg_str.split(":")
			reg = sublime.Region(int(reg_parts[0]), int(reg_parts[1]))
			self.view.replace(edit, reg, characters)

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
		4. "most common ancestor" between the file for the given view and the definition file

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

			# For sorting distances to the most common ancestor.
			# For example, the distance from "foo.txt" to "bar.txt" is 3,
			# while from "bar.txt" to "foo.txt" the distance is 2.
			#
			# /this/is/an/example/path/foo.txt
			# /this/is/another/path/bar.txt
			def get_dirs(path):
				ret = []
				path, base = os.path.split(path)
				while base != "":
					path, base = os.path.split(path)
					ret.append(base)
				ret.reverse()
				return ret
			fn_dirs = get_dirs(fn)
			def get_ancestor_dist(sym_loc):
				sym_dirs = get_dirs(sym_loc.path)
				for i in range(len(fn_dirs)):
					if sym_dirs[i] != fn_dirs[i]:
						break
				ancestor_dist = len(fn_dirs)-i
				return ancestor_dist

			# Build a collection of filters to find the most appropriate result.
			# We filter down until there aren't any sym_locs left, or we've run out of filters.
			# The order of the filters matters.
			filter_open    = lambda locs: list(filter(lambda sl: win.find_open_file(sl.path) != None, locs)) # presedence (2)
			filter_extents = lambda locs: list(filter(lambda sl: sl.path.endswith(ext), locs))               # presedence (3)
			sort_ancestor  = lambda locs: list(sorted(locs, key=get_ancestor_dist))                          # presedence (4)
			filters = [filter_open, filter_extents, sort_ancestor]

			# Find the best fitting sym_loc
			sym_loc = defs[0]
			for sym_filter in filters:
				defs_new = sym_filter(defs)
				if len(defs_new) != 0:
					defs = defs_new
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
				doc_regs, doc_strs, sym_locs = [], [], []
				for reg in view.sel():
					doc_str, sym_loc, sym_reg = self.build_doc_parts(view, reg.a)
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
			doc_str, sym_loc, sym_reg = self.build_doc_parts(view, reg.a)
			if doc_str != None:
				self.add_docs(view, [sym_reg], [doc_str], [sym_loc], is_double_click=True)

	def on_hover(self, view, point, hover_zone):
		if not self.setting("show_on_hover"):
			return

		doc_str, sym_loc, sym_reg = self.build_doc_parts(view, point)
		hover_line = view.rowcol(point)[0]

		if doc_str == None:
			if hover_line != self.hover_line:
				if self.setting("hover_auto_hide"):
					view.erase_regions("hd_hover")
		else:
			self.add_docs(view, [sym_reg], [doc_str], [sym_loc], is_hover=True)
		self.hover_line = hover_line

	def build_doc_parts(self, view, point, force_doc_string=None, force_interface=None, force_hyperlink=None, _look_behind=False):
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
				return self.build_doc_parts(view, point, force_doc_string, force_interface, force_hyperlink, _look_behind=True)
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
				return self.build_doc_parts(view, point, force_doc_string, force_interface, force_hyperlink, _look_behind=True)
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
		comment_str, comment_scopes = self.reduce_comment_str(v2, comment_str, comment_scopes)
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
		is_ctrl = self.is_ctrl_pressed() and self.setting("toggle_display_style") and not is_keybinding
		if (is_hover and not self.setting("hover_auto_hide")) or \
		   (is_double_click and not self.setting("double_click_auto_hide")) or \
		   (is_keybinding and not self.setting("keybinding_auto_hide")):
			auto_hide = False
		if not auto_hide or is_ctrl:
			close_button = f" <a href='close:!href!'>close</a>"
			doc_strs = list(map(lambda s: s+close_button, doc_strs))

		# add the link index to the href
		for i in range(len(doc_strs)):
			doc_strs[i] = doc_strs[i].replace("!href!", str(i))

		# determine the display style
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

	def reduce_comment_str(self, view, comment_str, comment_scopes):
		""" Removes the comment markings from the given comment string and trims the common leading
		whitespace off of the comment.

		Args:
		    view: the view that the comment_str was extracted from
		    comment_str: the comment to modify
		    comment_scopes: a list of [idx, len, scope_names] that matches the given comment_str
		Returns:
		    comment_str: The modified string value
		    comment_scopes: The modified scopes, whose regions have been modified to match the
		                    reduction in comment string lengths
		"""
		if len(comment_str.strip()) == 0:
			return comment_str, comment_scopes

		# prepare a system for tracking reductions
		# each reduction has the values "newpos", "oldpos", and "length"
		new_comment_scopes = []
		for cs in comment_scopes:
			end_pos = cs[0] + cs[1]
			new_comment_scopes.append([cs[0], cs[1], cs[2], end_pos])
		def reduce_string(strval, pos, length):
			if length == 0:
				return strval

			# first, apply the reduction to the comment_scopes
			to_remove = []
			for cs in new_comment_scopes:
				for p in [0, 3]:
					if cs[p] >= pos:
						if cs[p] >= pos+length:
							cs[p] -= length
						else:
							cs[p] = pos
				if cs[0] == cs[3]:
					to_remove.append(cs)
			for cs in to_remove:
				new_comment_scopes.remove(cs)

			# now reduce the string
			if pos == 0:
				return strval[length:]
			elif pos+length >= len(strval):
				return strval[:pos]
			else:
				return strval[:pos] + strval[pos+length:]

		# helpful string functions
		def split_line(strval, ws_loc="left"):
			""" Returns the preceeding whitespace, and the following rest of the string.

			Args:
			    ws_loc: "left" for normal operation, or "right" to instead return the
			            trailing whitespace and the preceeding rest of the string
		    Returns:
		        ws: the preceeding (or trailing) whitespace
		        nonws: the trailing (or preceeding) rest of the string
		    """
			if ws_loc == "left":
				non_whitespace = strval.lstrip()
				whitespace = strval[:len(strval) - len(non_whitespace)]
			else:
				non_whitespace = strval.rstrip()
				whitespace = strval[len(non_whitespace):]
			return whitespace, non_whitespace
		def remove_common_whitespace(comment_str):
			""" Removes the common leading whitespace from all lines. """
			# find the length of the common whitespace
			first_line_parts = split_line(v2.substr(v2.line(0)))
			common_whitespace = len(first_line_parts[0]) + len(first_line_parts[1])
			for line in v2.lines(sublime.Region(0, v2.size())):
				line_str = v2.substr(line)
				if len(line_str) == 0:
					continue
				line_parts = split_line(line_str)
				common_whitespace = min(common_whitespace, len(line_parts[0]))

			# remove up to the common whitespace
			pos = 0
			while pos < v2.size():
				line = v2.line(pos)
			strval = v2.substr(line)

			whitespace, non_whitespace = split_line(strval)
			linepos, cnt = 0, 0
			while linepos < len(whitespace):
				if whitespace[linepos] == "\t":
					cnt += tab_size
				else:
					cnt += 1
				if cnt > common_whitespace:
					break
				linepos += 1
			linepos =  min(linepos, len(whitespace))

			whitespace = whitespace[linepos:]
			comment_str = reduce_string(comment_str, pos, linepos)
			v2.run_command("hover_docs", args={ "mode": "replace", "reg_str": f"{line.a}:{line.b}", "characters": whitespace+non_whitespace })
				pos = v2.full_line(pos).b
			v2.run_command("hover_docs", args={ "mode": "replace", "reg_str": f"0:{v2.size()}", "characters": comment_str })

			return comment_str
		def remove_empty_lines(comment_str):
			empty_leading = 0
			for line in v2.lines(sublime.Region(0, v2.size())):
				line_str = v2.substr(line)
				if len(line_str.strip()) > 0:
					empty_leading = line.a
					break
			if empty_leading > 0:
				comment_str = reduce_string(comment_str, 0, empty_leading)
			empty_trailing_ws = split_line(comment_str, "right")[0]
			comment_str = reduce_string(comment_str, len(comment_str)-len(empty_trailing_ws), len(empty_trailing_ws))
			v2.run_command("hover_docs", args={ "mode": "replace", "reg_str": f"0:{v2.size()}", "characters": comment_str })
			return comment_str

		# build a new view into which to insert the comment string
		sublime.active_window().destroy_output_panel("hd_output_panel")
		v2 = sublime.active_window().create_output_panel("hd_output_panel", True)
		if view.syntax() != None:
			v2.assign_syntax(view.syntax())
		v2.set_scratch(True)

		# replace all the tabs in the comment string
		tab_size = view.settings().get("tab_size")
		tab_size = 4 if tab_size is None else tab_size
		tab_str = " "*tab_size
		tab_idx = comment_str.find("\t")
		while tab_idx >= 0:
			for cs in new_comment_scopes:
				for p in [0, 3]:
					if cs[p] > tab_idx:
						cs[p] += tab_size-1
			comment_str = comment_str[:tab_idx] + tab_str + comment_str[tab_idx+1:]
			tab_idx = comment_str.find("\t")

		# insert the comment string
		v2.run_command("hover_docs", args={ "mode": "append", "characters": comment_str })

		# remove white space 1
		comment_str = remove_empty_lines(comment_str)
		comment_str = remove_common_whitespace(comment_str)

		# remove language-specific multiline docstrings
		is_docstr, cm_start, cm_mid, cm_end = self.get_comment_is_docstring(comment_str, view)
		if is_docstr:
			# example:
			#     /* this
			#      * is
			#      * a comment */
			# =>
			#     this
			#     is
			#     a comment
			first_line_parts = split_line(v2.substr(v2.line(0)))
			last_line_parts  = split_line(v2.substr(v2.line(v2.size())), "right")
			start_ws = split_line(first_line_parts[1][len(cm_start):])[0]        # eg "/* start of comment" => " "
			end_ws   = split_line(last_line_parts[1][:-len(cm_end)], "right")[0] # eg "end of comment */" => " "
			start_ws_len = min(len(start_ws), 1) # don't remove more than one extra space
			comment_str = reduce_string(comment_str, len(first_line_parts[0]),                 len(cm_start)+start_ws_len)
			comment_str = reduce_string(comment_str, len(comment_str)-len(cm_end)-len(end_ws), len(cm_end)+len(end_ws))
			v2.run_command("hover_docs", args={ "mode": "replace", "reg_str": f"0:{v2.size()}", "characters": comment_str })
			if cm_mid != "":
				# deal with middle-line comment markings, for example "* i'm a c comment"
				pos = v2.size()
				while pos > 0:
					line = v2.line(pos)
					line_str = v2.substr(line)
					line_ws, line_nonws = split_line(line_str)
					if line_nonws.startswith(cm_mid):
						mid_ws = split_line(line_nonws[len(cm_mid):])[0]
						mid_ws_len = min(len(mid_ws), 1) # don't remove more than one extra space
						comment_str = reduce_string(comment_str, line.a, len(line_ws)+len(cm_mid)+mid_ws_len)
					pos = line.a-1
				v2.run_command("hover_docs", args={ "mode": "replace", "reg_str": f"0:{v2.size()}", "characters": comment_str })

		# remove the per-line comment markings from the comment string
		if not is_docstr:
			pos = 0
			while pos < v2.size():
				line = v2.line(pos)
				line_str = v2.substr(line)

				# replace the comment
				v2.sel().clear()
				v2.sel().add(pos)
				v2.run_command("toggle_comment")
				newline = v2.line(pos)
				if newline.size() < line.size():
					# track the string reduction
					newline_str = v2.substr(newline)
					front_cnt = max(0, line_str.index(newline_str))
					back_cnt = max(0, line.size()-newline.size() - front_cnt)
					comment_str = reduce_string(comment_str, line.a, front_cnt)
					comment_str = reduce_string(comment_str, line.b-front_cnt, back_cnt)
				else:
					# toggled comment the wrong way
					v2.run_command("hover_docs", args={ "mode": "replace", "reg_str": f"{newline.a}:{newline.b}", "characters": line_str })

				# move on to the next line
				pos = v2.full_line(pos).b+1
			v2.run_command("hover_docs", args={ "mode": "replace", "reg_str": f"0:{v2.size()}", "characters": comment_str })

		# remove white space 2
		comment_str = remove_empty_lines(comment_str)
		comment_str = remove_common_whitespace(comment_str)

		# update the "lengths" of the comment_scopes
		for cs in new_comment_scopes:
			cs[1] = cs[3] - cs[0]
			del cs[3]

		return comment_str, new_comment_scopes

	def get_comment_is_docstring(self, comment_str, view):
		""" Determine if the comment string is a doc string, and
		return the doc string markings for said comment.

		Args:
			comment_str: the comment string to inspect
			view: the view that the comment is located in (necessary to get the view's syntax name)
		Returns:
			is_docstr: True if the comment is a docstring, False otherwise
			cm_start: The opening docstring marking
			cm_mid: The per-line docstring marking (could be empty string)
			cm_end: The closing docstring marking
		"""
		multi_line_docstrings = self.setting("multi_line_docstrings")
		syntax_name = "" if view.syntax() is None else view.syntax().name.lower()
		if syntax_name in multi_line_docstrings:
			comment_str_rs = comment_str.rstrip()
			comment_str_ls = comment_str_rs.lstrip()
			for comment_markings in multi_line_docstrings[syntax_name]:
				cm_start, cm_end = comment_markings[0], comment_markings[1]
				cm_mid = "" if len(comment_markings) < 3 else comment_markings[2]
				if comment_str_ls.startswith(cm_start) and comment_str_rs.endswith(cm_end):
					return True, cm_start, cm_mid, cm_end
		return False, "", "", ""

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
				if 'foreground' in default_style and style['foreground'] == default_style['foreground']:
					style = tmp_style
				if 'foreground' in default_style and tmp_style['foreground'] != default_style['foreground']:
					style = tmp_style

			# apply the syntax for this piece
			style_str = f"<div style='display:inline;"
			if "foreground" in style:
				style_str += f" color:{style['foreground']};"
			if "background" in style:
				style_str += f" background-color:{style['background']};"
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

			# create a hidden output panel
			sublime.active_window().destroy_output_panel("hd_output_panel")
			v2 = sublime.active_window().create_output_panel("hd_output_panel", True)
			large_size = v2.settings()["syntax_detection_size_limit"]
			if os.path.getsize(sym_loc.path) < large_size:
				# if the file is small, the load the entire file
				with open(sym_loc.path, 'r') as f:
					v2.run_command("hover_docs", args={ "mode": "append", "characters": f.read() })
					pos = self.get_pos(v2, sym_loc.row, sym_loc.col)
					sym_reg = sublime.Region(pos, pos+len(sym_name))
					sym_line = v2.line(pos)
					pre_line = v2.line(sym_line.a-1)
					post_line = v2.line(v2.full_line(pos).b+1)
			else:
				# If the file is large, then just load the surrounding 100 lines on either side fo the symbol.
				# TODO load the megabyte surrounding the symbol instead, should be faster and hopefully have
				# significantly more context.
				start = max(0, sym_loc.row - 100)
				stop = sym_loc.row + 100
				pre_line, sym_line, post_line, sym_reg = 0, 0, 0, None
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
							v2.run_command("hover_docs", args={ "mode": "append", "characters": line })
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
			post_line = v2.line(v2.full_line(pos).b+1)

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
				# include the leading whitespace on the comment string
				comment_line = v2.line(comment_reg.a)
				pre_comment_str = v2.substr(sublime.Region(comment_line.a, comment_reg.a))
				if len(pre_comment_str.strip()) == 0:
					comment_reg = sublime.Region(comment_line.a, comment_reg.b)
				# comment found, stop looking
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
		""" Checks if cntl is being held down """
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

		# find the view with the gi