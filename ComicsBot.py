import discord, os, requests, atoma, feedparser, logging, asyncio, json, ssl, sys
from datetime import datetime
from discord.utils import find
from dotenv import load_dotenv

load_dotenv()

dir = os.path.realpath(os.path.dirname(__file__))

logging.basicConfig(level=os.environ.get('LOGLEVEL', os.getenv('LOG-LEVEL')))
logger = logging.getLogger('discord')
logger.setLevel(os.getenv('LOG-LEVEL').strip())
logging.getLogger('discord.http').setLevel(os.getenv('LOG-LEVEL').strip())
handler = logging.FileHandler(filename=str(os.path.join(dir, 'discord.log')), encoding='utf-8', mode='w')
handler.setFormatter(logging.Formatter('%(asctime)s:%(levelname)s:%(name)s: %(message)s'))
logger.addHandler(handler)
logger.debug('logger level: ' + str(logger.level))
intents = discord.Intents(messages=True, guilds=True, reactions=True)
intents.message_content = True
availableComics = {}
guildComics = {}

with open(os.path.join(dir, 'comics.json')) as f:
    availableComics = json.loads(f.read())
                
class ComicsBot(discord.Client):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs, intents=intents)

    async def on_ready(self):
        logger.debug("We have logged in as {0.user}".format(client))
        logger.debug("check for comics every " + os.getenv('INTERVAL') + " seconds")
        for guild in self.guilds:
            logger.debug("starting for guild: "+guild.name)
            mode = 'r'
            if not os.path.exists("% s" % guild.id+'-Comics.json'):
                mode = 'w+'
            if (mode == 'r'):
                with open(os.path.join(dir, "% s" % guild.id+'-Comics.json'), mode) as comics:
                    guildComics[guild.id] = json.loads(comics.read())
            else:
                with open(os.path.join(dir, "% s" % guild.id+'-Comics.json'), mode) as comics:
                    comics.write('{}')
                    guildComics[guild.id] = {}
        await self.checkForDailyUpdates()

    async def on_guild_join(self, guild):
        general = find(lambda x: x.name == 'general',  guild.text_channels)
        if general and general.permissions_for(guild.me).send_messages:
            await general.send('Hello!\n\nI\'m the ComicsBot!\n\nFor more information type "!help"!')
            
    def writeAvailableComicsToGuildJsonFile(self, guildId):
        if (guildComics[guildId]) :
            logger.debug("Writing to guild file! \n" + json.dumps(guildComics[guildId]))
            with open(os.path.join(dir, "% s" % guildId+'-Comics.json'), 'w+') as jf:
                jf.write(json.dumps(guildComics[guildId], indent=4, sort_keys=True))

    def updateEnv(self):
        envOutput = []
        with open(os.path.join(dir, '.env'), 'w+') as jf:
            for key in os.environ:
                if key == 'TOKEN' or key.startswith('COMICS-CHANNEL'):
                    envOutput.append(key+':'+os.getenv(key))
            jf.write(''.join(envOutput))
    
    async def fetchComic(self, url):
        response = ''
        feed = ''
        name = ''
        link = ''
        ssl._create_default_https_context = ssl._create_unverified_context
        logger.debug('Trying to fetch from ' + url)
        foundLink = True
        try:
            feed = feedparser.parse(url)
            name = feed.entries[0].title
        except:
            try:
                response = requests.get(url)
                feed = atoma.parse_atom_bytes(response.content)
                name = feed.entries[0].title.value
            except: 
                foundLink = False
        if (foundLink):
            link = feed.entries[0].links[0].href
            return name + " - " + link
        else :
            return ''

    async def on_message(self, message):
        if message.author == client.user or message.guild == None:
            return
        
        incomingGuildId = message.guild.id
        incomingChannelId = message.channel.id
        incomingChannelName = message.channel
        outgoingChannelId = int(os.getenv('COMICS-CHANNEL-'+"% s" % incomingGuildId))
        if outgoingChannelId == None or incomingChannelId == outgoingChannelId:
            if message.content.startswith("!scc") and message.author.guild_permissions.administrator:
                if(outgoingChannelId != None):
                    comicsChannel = self.get_channel(int(outgoingChannelId))
                    await comicsChannel.send('Channel '+"% s" % comicsChannel+' is registered as comics channel for current server!')
                else:
                    await message.channel.send('No channel has been set as dedicated comics channel!')
                    
            if message.content.startswith("!sbc") and message.author.guild_permissions.administrator:
                if(outgoingChannelId != None):
                    channel = self.get_channel(int(incomingChannelId))
                    comicsChannel = self.get_channel(int(outgoingChannelId))
                    await channel.send('Channel '+"% s" % comicsChannel+' is already registered as comics channel for current server!')
                else :
                    os.environ['COMICS-CHANNEL-'+"% s" % incomingGuildId] = "% s" % incomingChannelId
                    self.updateEnv()
                    channel = self.get_channel(int(incomingChannelId))
                    self.writeAvailableComicsToGuildJsonFile(incomingGuildId)
                    await channel.send('Setting up this channel ('+"% s" % incomingChannelName+') as dedicated comics channel!\nThe bot will no longer listen to commands in other channels!')

            if message.content.startswith("!dbc") and message.author.guild_permissions.administrator:
                channel = self.get_channel(int(incomingChannelId))
                if(outgoingChannelId == None):
                    await channel.send('Channel doesn\'t exists in registry!')
                elif (int(incomingChannelId) != int(outgoingChannelId)): 
                    await channel.send('Channel '+"% s" % incomingChannelName+' is not registered as comics channel for current server!')
                    comicsChannel = self.get_channel(int(outgoingChannelId))
                    await channel.send('Channel '+"% s" % comicsChannel+' is registered as comics channel for current server!')
                else :
                    del os.environ['COMICS-CHANNEL-'+"% s" % incomingGuildId]
                    self.updateEnv()
                    await channel.send('Channel '+"% s" % incomingChannelName+' has been removed from registry!')
                    await channel.send('To register a channel please type "!sbc" in the desired channel!')
            
            if message.content.startswith('!restart-bot') and message.author.display_name == 'dxcufgb':
                channel = self.get_channel(int(outgoingChannelId))
                await channel.send('Restarting...')
                os.execv(sys.executable, ['python'] + sys.argv)
                
            if (outgoingChannelId == None):
                channel = self.get_channel(int(incomingChannelId))
            else:
                channel = self.get_channel(int(outgoingChannelId))
                
            if message.content.startswith('!add-comic'):
                exists = False
                existingName = ''
                values = {}
                comic = {}
                innerValues = {}
                guildValues = {}
                guildValues['LastURL'] = ''
                inputMessage = message.content[13:]
                splitMessage = inputMessage.split('--')
                values[splitMessage[0].split(' ')[0]] = splitMessage[0].split(' ')[1]
                values[splitMessage[1].split(' ')[0]] = splitMessage[1].split(' ')[1]
                for comic in availableComics:
                    if availableComics[comic]['URL'] == values['url']:
                        exists = True
                        existingName = comic
                if not exists:
                    innerValues['URL'] = values['url']
                    guildComics[incomingGuildId][values['name']] = guildValues
                    availableComics[values['name']] = innerValues
                    self.updateComicsFile()
                else: 
                    guildComics[incomingGuildId][existingName] = guildValues
                self.writeAvailableComicsToGuildJsonFile(incomingGuildId)
                await channel.send('Comic "'+ values['name'] + '" added!')
            
            if message.content.startswith('!subscribe-comic'):
                guildValues = {}
                guildValues['LastURL'] = ''
                inputMessage = message.content[17:]
                if (inputMessage in availableComics):
                    guildComics[incomingGuildId][inputMessage] = guildValues
                    self.writeAvailableComicsToGuildJsonFile(incomingGuildId)
                    await channel.send('Subscribed to comic "'+ inputMessage + '"!')
                else :
                    await channel.send('Could not find comic "'+ inputMessage + '"!\n Did you check !available-comics for the correct name?')
                    
            if message.content.startswith('!remove-comic'):
                inputMessage = message.content[14:]
                del guildComics[incomingGuildId][inputMessage]
                self.writeAvailableComicsToGuildJsonFile(incomingGuildId)
                await channel.send('Comic with key '+inputMessage+' removed')
                    
            if message.content.startswith('!active-comics'):
                text = ''
                for comic in guildComics[incomingGuildId]:
                    text += comic+': '+availableComics[comic]['URL']+'\n'
                await channel.send(text)
            
            if message.content.startswith('!available-comics'):
                text = ''
                for comic in availableComics:
                    text += comic+':'+availableComics[comic]['URL']+'\n'
                await channel.send(text)

            if message.content.startswith('!help'):
                text = "|\n"
                if message.author.guild_permissions.administrator :
                    text += '=====================LAST CHANGE MADE 2023-12-24====================='
                    text += '==============================ADMIN ONLY==============================\n'
                    text += '!sbc\n'
                    text += 'Set Bot Channel: assigns bot to current channel!\n'
                    text += 'All comics will be posted in this channel!\n'
                    text += 'The bot will stop listening to other channels on this server!\n'
                    text += '\n'
                    text += '!scc\n'
                    text += 'Show Current Channel: Shows current channel used by the bot!\n'
                    text += '\n'
                    text += '!dbc\n'
                    text += 'Delete Bot Channel: Deletes the assigned channel so the bot can be moved!\n'
                    text += 'The bot will start listening to other channels again!\n'
                    text += '\n'
                    text += '==============================EVERYONE==============================\n'
                text += '!add-comic --name [NAME] --url [URL TO RSS OR ATOM FEED]\n'
                text += 'Add a comic with assigned name and point to URL\n'
                text += '\n'
                text += '!active-comics\n'
                text += 'List active comics name and URL for this server\n'
                text += '\n'
                text += '!available-comics\n'
                text += 'List available comics name and URL for the bot\n'
                text += '\n'
                text += '!subscribe-comic [NAME]\n'
                text += 'Subscribe to comic using the name from !available-comics\n'
                text += '\n'
                text += '!remove-comic [NAME]\n'
                text += 'Remove comic with the assigned name, use "!active-comics" to list comics name and URLs\n'
                text += '\n'
                text += '!help\n'
                text += 'shows this help section!\n'
                text += '\n'
                text += '\n'
                text += 'Created by Dxcufgb (2022 - ' + str(datetime.today().year) + ')'
                await channel.send(text)

            if message.content.startswith('!show-env') and message.author.display_name == 'dxcufgb':
                os.environ
                await channel.send(os.environ)
    
    async def checkForDailyUpdates(self):
        await self.wait_until_ready()
        while not self.is_closed():
            logger.debug('Fetching comics links!')
            for comic in availableComics:
                logger.debug('Fetching '+comic+'!')
                try:
                    fetchedLink = await self.fetchComic(availableComics[comic]['URL'])
                except:
                    logger.error("error occured fetching comic: "+comic+" with URL: "+availableComics[comic]['URL'])
                    fetchedLink = availableComics[comic]['LastURL']
                if (fetchedLink != ''):
                    if ('lastURL' not in availableComics[comic]):
                        availableComics[comic]['LastURL'] = fetchedLink
                    if (fetchedLink != availableComics[comic]['LastURL']):
                        availableComics[comic]['LastURL'] = fetchedLink
                    self.updateComicsFile()
                    await self.sendToAllGuilds()
                
            hour = datetime.today().hour            
            if os.getenv("CHRISTMAS_WISH") == '0' and datetime.today().strftime('%m-%d') == '12-24' and hour > 11 and hour < 15 :
                os.environ["CHRISTMAS_WISH"] = '1'
                self.updateEnv()
                await self.sendChristmasWishToAllGuilds()
            if os.getenv("CHRISTMAS_WISH") == '1' and datetime.today().strftime('%m') == '01' :
                os.environ["CHRISTMAS_WISH"] = "0"
                self.updateEnv()
            logger.debug('Idling for ' + os.getenv('INTERVAL') + ' seconds...')
            await asyncio.sleep(int(os.getenv('INTERVAL')))

    async def sendToAllGuilds(self):
        for guild in self.guilds:
            logger.debug('running task for guild: '+"% s" % guild.id)
            channel = self.get_channel(int(os.getenv('COMICS-CHANNEL-'+"% s" % guild.id).strip()))
            for co in guildComics[guild.id]:
               if (co in guildComics[guild.id] and availableComics[co]['LastURL'] != guildComics[guild.id][co]['LastURL']):
                   logger.debug('Sending URL: ' + availableComics[co]['LastURL'])
                   await channel.send(availableComics[co]['LastURL'])
                   guildComics[guild.id][co]['LastURL'] = availableComics[co]['LastURL']
            if (guildComics[guild.id] != ''):
               self.writeAvailableComicsToGuildJsonFile(guild.id)

    async def sendChristmasWishToAllGuilds(self):
        for guild in self.guilds:
            logger.debug('running christmas task for guild: '+"% s" % guild.id)
            channel = self.get_channel(int(os.getenv('COMICS-CHANNEL-'+"% s" % guild.id).strip()))
            await channel.send("https://tenor.com/view/merry-christmas-gif-church-gif-10346745427738870662")
    
    def updateComicsFile(self):
        with open(os.path.join(dir, 'comics.json'), 'w+') as jf:
            jf.write(json.dumps(availableComics, indent=4, sort_keys=True))
        
client = ComicsBot()
client.run(os.getenv('TOKEN'))
