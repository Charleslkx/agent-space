function Table(t)
  local n = #t.colspecs
  if n == 0 then return nil end
  for i = 1, n do
    t.colspecs[i][2] = 1.0 / n
  end
  return t
end

function Para(p)
  if #p.content == 1 and p.content[1].tag == "Image" then
    return {
      pandoc.RawBlock("latex", "\\begin{center}"),
      pandoc.Plain({p.content[1]}),
      pandoc.RawBlock("latex", "\\end{center}")
    }
  end
end
